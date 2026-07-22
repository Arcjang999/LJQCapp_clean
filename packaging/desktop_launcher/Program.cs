using System.Diagnostics;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace LJQCApp.Desktop;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        LauncherOptions options = LauncherOptions.Parse(args);
        if (options.MaintenanceArguments.Count > 0)
        {
            return MaintenanceCommandRunner.Run(options.MaintenanceArguments);
        }

        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        Application.Run(new LauncherForm(options));
        return 0;
    }
}

internal sealed class LauncherForm : Form
{
    private readonly LauncherOptions _options;
    private readonly Label _statusLabel;
    private Process? _serviceProcess;
    private ServiceExecutable? _serviceExecutable;
    private WebView2? _webView;
    private bool _shutdownStarted;
    private int _port;

    public LauncherForm(LauncherOptions options)
    {
        _options = options;
        Text = "LJQCApp";
        Width = 1440;
        Height = 960;
        MinimumSize = new Size(1100, 760);
        StartPosition = FormStartPosition.CenterScreen;

        _statusLabel = new Label
        {
            Dock = DockStyle.Fill,
            Text = "正在启动 LJQCApp...",
            TextAlign = ContentAlignment.MiddleCenter,
            Font = new Font("Microsoft YaHei UI", 16, FontStyle.Bold),
        };
        Controls.Add(_statusLabel);

        Shown += async (_, _) => await InitializeAsync();
        FormClosed += (_, _) => ShutdownService();
    }

    private async Task InitializeAsync()
    {
        try
        {
            _port = _options.Port ?? GetFreeLoopbackPort();
            Log($"Launcher starting. target port={_port}");

            _serviceExecutable = ServiceExecutableResolver.Resolve();
            _serviceProcess = StartServiceProcess(_serviceExecutable.Path, _port);

            await WaitForServiceAsync(_serviceProcess, _port);
            await InitializeWebViewAsync(_port);
            ScheduleAutoCloseIfRequested();
        }
        catch (Exception ex)
        {
            Log($"Startup failed: {ex}");
            MessageBox.Show(
                this,
                "LJQCApp 启动失败。\n\n请确认 WebView2 Runtime 可用，并查看 %LOCALAPPDATA%\\LJQCApp\\desktop_launcher.log 和 launcher.log。",
                "LJQCApp",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            Close();
        }
    }

    private static Process StartServiceProcess(string servicePath, int port)
    {
        ProcessStartInfo startInfo = new()
        {
            FileName = servicePath,
            UseShellExecute = false,
            CreateNoWindow = true,
            WorkingDirectory = Path.GetDirectoryName(servicePath) ?? AppContext.BaseDirectory,
        };
        startInfo.ArgumentList.Add("--port");
        startInfo.ArgumentList.Add(port.ToString());
        startInfo.ArgumentList.Add("--address");
        startInfo.ArgumentList.Add("127.0.0.1");
        startInfo.Environment["LJQCAPP_LAUNCH_MODE"] = "desktop";

        Process process = Process.Start(startInfo)
            ?? throw new InvalidOperationException("Failed to start LJQCApp service process.");

        return process;
    }

    private async Task WaitForServiceAsync(Process process, int port)
    {
        using HttpClient client = new()
        {
            Timeout = TimeSpan.FromSeconds(2),
        };

        Uri healthUri = new($"http://127.0.0.1:{port}/_stcore/health");
        DateTime deadline = DateTime.UtcNow.AddSeconds(90);

        while (DateTime.UtcNow < deadline)
        {
            if (process.HasExited)
            {
                throw new InvalidOperationException(
                    $"LJQCApp service exited early with code {process.ExitCode}. See launcher.log for details."
                );
            }

            try
            {
                using HttpResponseMessage response = await client.GetAsync(healthUri);
                if (response.IsSuccessStatusCode)
                {
                    Log($"Service became healthy on port {port}");
                    return;
                }
            }
            catch
            {
                // Ignore probe failures while the service is still warming up.
            }

            await Task.Delay(500);
        }

        throw new TimeoutException($"Timed out waiting for LJQCApp service on port {port}.");
    }

    private async Task InitializeWebViewAsync(int port)
    {
        string userDataFolder = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "LJQCApp",
            "WebView2"
        );
        Directory.CreateDirectory(userDataFolder);

        _webView = new WebView2
        {
            Dock = DockStyle.Fill,
            CreationProperties = new CoreWebView2CreationProperties
            {
                UserDataFolder = userDataFolder,
            },
        };

        Controls.Clear();
        Controls.Add(_webView);

        await _webView.EnsureCoreWebView2Async();
        _webView.CoreWebView2.Settings.AreBrowserAcceleratorKeysEnabled = true;
        _webView.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
        _webView.CoreWebView2.Settings.IsStatusBarEnabled = false;
        _webView.Source = new Uri($"http://127.0.0.1:{port}");

        Log($"WebView attached to http://127.0.0.1:{port}");
        _ = VerifySidebarNavigationHiddenAsync();
    }

    private void ScheduleAutoCloseIfRequested()
    {
        if (_options.AutoCloseSeconds <= 0)
        {
            return;
        }

        System.Windows.Forms.Timer timer = new()
        {
            Interval = _options.AutoCloseSeconds * 1000,
        };
        timer.Tick += (_, _) =>
        {
            timer.Stop();
            timer.Dispose();
            Close();
        };
        timer.Start();
    }

    private async Task VerifySidebarNavigationHiddenAsync()
    {
        if (_webView?.CoreWebView2 is null)
        {
            return;
        }

        try
        {
            await Task.Delay(4000);
            string result = await _webView.CoreWebView2.ExecuteScriptAsync(
                """
                (() => {
                    const nav = document.querySelector('[data-testid="stSidebarNav"]');
                    return JSON.stringify({
                        hasSidebarNav: !!nav,
                        sidebarNavText: nav ? nav.innerText : ''
                    });
                })();
                """
            );
            Log($"Sidebar nav probe: {result}");
        }
        catch (Exception ex)
        {
            Log($"Sidebar nav probe warning: {ex.Message}");
        }
    }

    private void ShutdownService()
    {
        if (_shutdownStarted)
        {
            return;
        }

        _shutdownStarted = true;
        Log("Shutting down launcher and service process tree.");

        try
        {
            if (_serviceProcess is { HasExited: false })
            {
                _serviceProcess.Kill(entireProcessTree: true);
                _serviceProcess.WaitForExit(10000);
                Log("Service process tree terminated.");
            }
        }
        catch (Exception ex)
        {
            Log($"Service shutdown warning: {ex}");
        }
        finally
        {
            _serviceProcess?.Dispose();
            _serviceProcess = null;
        }

        _serviceExecutable?.Cleanup();
    }

    private static int GetFreeLoopbackPort()
    {
        TcpListener listener = new(IPAddress.Loopback, 0);
        listener.Start();
        int port = ((IPEndPoint)listener.LocalEndpoint).Port;
        listener.Stop();
        return port;
    }

    private static void Log(string message) => LauncherLog.Write(message);
}

internal static class MaintenanceCommandRunner
{
    public static int Run(IReadOnlyList<string> arguments)
    {
        ServiceExecutable? serviceExecutable = null;
        try
        {
            serviceExecutable = ServiceExecutableResolver.Resolve();
            ProcessStartInfo startInfo = new()
            {
                FileName = serviceExecutable.Path,
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = Path.GetDirectoryName(serviceExecutable.Path) ?? AppContext.BaseDirectory,
            };
            foreach (string argument in arguments)
            {
                startInfo.ArgumentList.Add(argument);
            }

            Process process = Process.Start(startInfo)
                ?? throw new InvalidOperationException("Failed to start LJQCApp maintenance command.");
            process.WaitForExit();
            return process.ExitCode;
        }
        catch (Exception ex)
        {
            LauncherLog.Write($"Maintenance command failed: {ex}");
            return 1;
        }
        finally
        {
            serviceExecutable?.Cleanup();
        }
    }
}

internal static class ServiceExecutableResolver
{
    public static ServiceExecutable Resolve()
    {
        string? embeddedResourceName = Assembly
            .GetExecutingAssembly()
            .GetManifestResourceNames()
            .FirstOrDefault(name => name.EndsWith("LJQCAppService.exe", StringComparison.OrdinalIgnoreCase));

        if (!string.IsNullOrWhiteSpace(embeddedResourceName))
        {
            string tempRoot = Path.Combine(
                Path.GetTempPath(),
                "LJQCApp",
                "embedded_service",
                Guid.NewGuid().ToString("N")
            );
            Directory.CreateDirectory(tempRoot);

            string extractedPath = Path.Combine(tempRoot, "LJQCAppService.exe");
            using Stream resourceStream = Assembly.GetExecutingAssembly().GetManifestResourceStream(embeddedResourceName)
                ?? throw new FileNotFoundException("Embedded service resource not found.", embeddedResourceName);
            using FileStream outputStream = File.Create(extractedPath);
            resourceStream.CopyTo(outputStream);

            LauncherLog.Write($"Extracted embedded service to {extractedPath}");
            return new ServiceExecutable(extractedPath, tempRoot);
        }

        string baseDirectory = AppContext.BaseDirectory;
        string[] candidates =
        [
            Path.Combine(baseDirectory, "_internal", "service", "LJQCAppService.exe"),
            Path.Combine(baseDirectory, "service", "LJQCAppService.exe"),
            Path.Combine(baseDirectory, "LJQCAppService.exe"),
        ];

        foreach (string candidate in candidates)
        {
            if (File.Exists(candidate))
            {
                LauncherLog.Write($"Using adjacent service executable {candidate}");
                return new ServiceExecutable(candidate, null);
            }
        }

        throw new FileNotFoundException("Could not locate LJQCAppService.exe.", candidates[0]);
    }
}

internal sealed record ServiceExecutable(string Path, string? TemporaryDirectory)
{
    public void Cleanup()
    {
        if (string.IsNullOrWhiteSpace(TemporaryDirectory))
        {
            return;
        }

        for (int attempt = 1; attempt <= 10; attempt += 1)
        {
            try
            {
                if (Directory.Exists(TemporaryDirectory))
                {
                    Directory.Delete(TemporaryDirectory, recursive: true);
                }

                LauncherLog.Write($"Deleted temporary service directory {TemporaryDirectory}");
                return;
            }
            catch (Exception ex) when (attempt < 10)
            {
                Thread.Sleep(500);
                LauncherLog.Write($"Temporary service cleanup retry {attempt}: {ex.Message}");
            }
            catch (Exception ex)
            {
                LauncherLog.Write($"Temporary service cleanup warning: {ex}");
            }
        }
    }
}

internal sealed record LauncherOptions(int? Port, int AutoCloseSeconds, IReadOnlyList<string> MaintenanceArguments)
{
    public static LauncherOptions Parse(string[] args)
    {
        int? port = null;
        int autoCloseSeconds = 0;
        List<string> maintenanceArguments = [];

        for (int index = 0; index < args.Length; index += 1)
        {
            string argument = args[index];
            if (argument == "--port" && index + 1 < args.Length && int.TryParse(args[index + 1], out int parsedPort))
            {
                port = parsedPort;
                index += 1;
                continue;
            }

            if (
                argument == "--auto-close-seconds"
                && index + 1 < args.Length
                && int.TryParse(args[index + 1], out int parsedSeconds)
            )
            {
                autoCloseSeconds = Math.Max(parsedSeconds, 0);
                index += 1;
                continue;
            }

            if (argument is "--reset-db" or "--seed-demo")
            {
                maintenanceArguments.Add(argument);
                continue;
            }

            bool isMaintenanceOption = argument is "--replace-demo" or "--demo-profile" or "--profile";
            if (isMaintenanceOption && maintenanceArguments.Count > 0)
            {
                maintenanceArguments.Add(argument);
                bool optionExpectsValue = argument is "--demo-profile" or "--profile";
                if (optionExpectsValue && index + 1 < args.Length)
                {
                    maintenanceArguments.Add(args[index + 1]);
                    index += 1;
                }
            }
        }

        return new LauncherOptions(port, autoCloseSeconds, maintenanceArguments);
    }
}

internal static class LauncherLog
{
    public static void Write(string message)
    {
        string logDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "LJQCApp"
        );
        Directory.CreateDirectory(logDirectory);

        string logPath = Path.Combine(logDirectory, "desktop_launcher.log");
        File.AppendAllText(
            logPath,
            $"[{DateTime.Now:yyyy-MM-dd HH:mm:ss}] {message}{Environment.NewLine}",
            Encoding.UTF8
        );
    }
}
