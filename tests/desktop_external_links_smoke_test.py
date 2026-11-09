"""Desktop link routing, with no Qt import, browser launch or native window."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE = ROOT / 'packaging/desktop_qt_launcher.py'
spec = importlib.util.spec_from_file_location('desktop_external_links_launcher', SOURCE)
launcher = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(launcher)

ORIGIN = 'http://127.0.0.1:18501'


class FakeSignal:
    def connect(self, callback):
        self.callback = callback

    def emit(self, value):
        self.callback(value)


class FakePage:
    def __init__(self, profile, parent):
        self.used_profile, self.parent = profile, parent
        self.newWindowRequested = FakeSignal()
        self.frame_requests = []
        self.downloads = []

    def download(self, destination):
        self.downloads.append(destination)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        self.frame_requests.append((url, navigation_type, is_main_frame))
        return 'unchanged-frame-policy'


def make_page():
    profile = object()
    view = SimpleNamespace(page=lambda: SimpleNamespace(profile=lambda: profile))
    opened = Mock()
    page = launcher._create_desktop_page(FakePage, view, ORIGIN, opened)
    return page, profile, opened


def url(value):
    return SimpleNamespace(toString=lambda: value)


class DesktopExternalLinkTests(unittest.TestCase):
    def test_same_origin_normal_navigation_fragment_and_reports_remain_internal(self):
        for destination in (ORIGIN, ORIGIN+'/', ORIGIN+'/media/report.pdf?x=1',
                            ORIGIN+'/#quality', '/_stcore/health', '#quality',
                            'blob:'+ORIGIN+'/report-id', 'about:blank', 'about:srcdoc'):
            with self.subTest(destination=destination):
                self.assertEqual('internal', launcher._desktop_navigation_action(destination, ORIGIN))

    def test_external_web_hosts_and_other_ports_are_not_app_navigation(self):
        for destination in ('https://www.nhc.gov.cn/standard.pdf',
                            'https://openstd.samr.gov.cn/', '//www.nhc.gov.cn/standard',
                            'http://127.0.0.1:18502/', 'http://127.0.0.1.example.com:18501/'):
            with self.subTest(destination=destination):
                self.assertEqual('external', launcher._desktop_navigation_action(destination, ORIGIN))

    def test_non_web_malformed_and_foreign_blob_urls_never_go_to_the_os(self):
        for destination in ('', 'file:///etc/passwd', 'javascript:alert(1)',
                            'mailto:a@example.com', 'data:text/html,test',
                            'https://user:secret@example.com/', 'https://example.com:bad/',
                            'https://example.com/\nnext', 'blob:https://example.com/id'):
            with self.subTest(destination=destination):
                self.assertEqual('blocked', launcher._desktop_navigation_action(destination, ORIGIN))

    def test_target_blank_external_link_opens_browser_without_navigating_app(self):
        page, profile, opened = make_page()
        destination = url('https://www.nhc.gov.cn/standard.pdf')
        accept = Mock()
        page.newWindowRequested.emit(SimpleNamespace(requestedUrl=lambda: destination, openIn=accept))
        opened.assert_called_once_with(destination)
        accept.assert_not_called()
        self.assertEqual([], page.downloads)
        self.assertIs(profile, page.used_profile)

    def test_same_window_external_link_opens_browser_and_rejects_app_navigation(self):
        page, _, opened = make_page()
        destination = url('https://www.nhc.gov.cn/standard.pdf')
        self.assertFalse(page.acceptNavigationRequest(destination, 'link-click', True))
        opened.assert_called_once_with(destination)

    def test_local_report_popup_downloads_without_replacing_the_application_page(self):
        page, profile, opened = make_page()
        for text in (ORIGIN+'/media/report.pdf', 'blob:'+ORIGIN+'/report-id'):
            destination = url(text)
            accept = Mock()
            page.newWindowRequested.emit(SimpleNamespace(requestedUrl=lambda: destination, openIn=accept))
            accept.assert_not_called()
            self.assertIs(destination, page.downloads[-1])
        opened.assert_not_called()
        self.assertEqual([], page.frame_requests)
        self.assertIs(profile, page.used_profile)

    def test_inline_pdf_popup_uses_download_even_when_url_is_not_a_media_endpoint(self):
        page, _, opened = make_page()
        destination = url(ORIGIN+'/reports/preview?id=1')
        replace_page = Mock()
        page.newWindowRequested.emit(SimpleNamespace(requestedUrl=lambda: destination, openIn=replace_page))
        self.assertEqual([destination], page.downloads)
        replace_page.assert_not_called()
        opened.assert_not_called()

    def test_empty_blank_and_unsupported_popups_cannot_replace_the_application(self):
        page, _, opened = make_page()
        for text in ('', 'about:blank', 'about:srcdoc', 'file:///tmp/a', 'javascript:alert(1)'):
            accept = Mock()
            page.newWindowRequested.emit(SimpleNamespace(requestedUrl=lambda: url(text), openIn=accept))
            accept.assert_not_called()
        opened.assert_not_called()
        self.assertEqual([], page.downloads)

    def test_local_top_level_and_component_frame_navigation_do_not_launch_browser(self):
        page, _, opened = make_page()
        self.assertTrue(page.acceptNavigationRequest(url(ORIGIN+'/'), 'other', True))
        destination = url('data:text/html,<p>inline component</p>')
        self.assertEqual('unchanged-frame-policy', page.acceptNavigationRequest(destination, 'other', False))
        self.assertEqual([(destination, 'other', False)], page.frame_requests)
        opened.assert_not_called()

    def test_launcher_installs_router_before_existing_profile_download_signal(self):
        tree = ast.parse(SOURCE.read_text())
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_run_qt_desktop')
        source = ast.get_source_segment(SOURCE.read_text(), run)
        self.assertIn('QDesktopServices.openUrl(url)', source)
        self.assertIn('view.setPage(_create_desktop_page(', source)
        self.assertIn('view.page().profile().downloadRequested.connect(handle_download)', source)
        self.assertLess(source.index('view.setPage('), source.index('downloadRequested.connect'))
        self.assertIn('请复制以下链接到浏览器打开：', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
