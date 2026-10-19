"""The maintained first-use guide loads in source and in a staged release layout."""
from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest
from ui import user_guide

PICTURE_NAMES = ('01-navigation.png', '02-new-project.png', '03-quality-standard.png')

HELP_APP = '''
import streamlit as st
from ui.user_guide import render_user_guide_page
if st.button('切换阅读方式', key='switch_help'):
    st.session_state['user_guide_practical'] = not st.session_state.get('user_guide_practical', False)
render_user_guide_page()
'''


def release_source_datas():
    """Run the real spec's resource collector without invoking a build or hooks."""
    tree = ast.parse((ROOT / 'packaging' / 'LJQCApp.spec').read_text(encoding='utf-8'))
    names = {'PROJECT_MODULE_FILES', 'PROJECT_PACKAGES', 'PROJECT_DATA_DIRS', 'PROJECT_HELP_FILES'}
    nodes = [node for node in tree.body if
        (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names
                                            for target in node.targets)) or
        (isinstance(node, ast.FunctionDef) and node.name == 'collect_project_source_datas')]
    namespace = {'PROJECT_ROOT': ROOT}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<release resource collector>', 'exec'), namespace)
    return namespace['collect_project_source_datas']


class UserGuideSmokeTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.stage = Path(self.temp.name) / '_internal' / 'app'
        self.stage.mkdir(parents=True)
        self.collector = release_source_datas()
        resources = self.collector()
        self.help_resources = [(Path(source), Path(destination)) for source, destination in resources
            if Path(source).name in {'OPERATION_GUIDE.md', 'current_user_guide.md', 'user_guide.py', *PICTURE_NAMES}]
        for source, destination in self.help_resources:
            target = self.stage / destination / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.module_path = self.stage / 'ui' / 'user_guide.py'
        self.guide_path = self.stage / 'docs' / 'OPERATION_GUIDE.md'
        self.pictures = [self.stage / 'docs' / 'images' / 'first-use' / name for name in PICTURE_NAMES]

    def tearDown(self):
        self.temp.cleanup()

    def test_release_collector_carries_readable_help_and_index_links(self):
        self.assertTrue(self.module_path.is_file())
        self.assertTrue(self.guide_path.is_file())
        self.assertTrue((self.stage / 'docs' / 'current_user_guide.md').is_file())
        with patch.object(user_guide, '__file__', str(self.module_path)):
            bundled = user_guide.load_operation_guide()
        source = user_guide.load_operation_guide(ROOT / 'docs' / 'OPERATION_GUIDE.md')
        for attribute in ('title', 'body', 'introduction', 'sections'):
            self.assertEqual(getattr(bundled, attribute), getattr(source, attribute))
        for bundled_picture, name in zip(self.pictures, PICTURE_NAMES):
            self.assertEqual(bundled_picture.read_bytes(),
                             (ROOT / 'docs' / 'images' / 'first-use' / name).read_bytes())
            reference = 'images/first-use/' + name
            self.assertEqual(user_guide._help_image_path(bundled.resource_dir, reference), bundled_picture.resolve())
        self.assertEqual(source.sections[0][0], '首次使用')
        index = (self.stage / 'docs' / 'current_user_guide.md').read_text(encoding='utf-8')
        targets = re.findall(r'\]\(OPERATION_GUIDE\.md#([^\)]+)\)', index)
        self.assertEqual(len(targets), len(source.sections))
        anchors = {re.sub(r'[^\w\s-]', '', title.lower()).replace(' ', '-')
                   for title, _ in source.sections}
        self.assertEqual(set(targets), anchors)
        # A missing canonical file must stop resource collection, not make a
        # release that silently falls back to stale or incomplete instructions.
        with patch.dict(self.collector.__globals__, {'PROJECT_ROOT': self.stage}):
            for required in (self.guide_path, self.pictures[0]):
                original = required.read_bytes()
                required.unlink()
                with self.assertRaises(FileNotFoundError):
                    self.collector()
                required.write_bytes(original)

    def test_both_help_modes_follow_the_same_file_after_a_content_update(self):
        with patch.object(user_guide, '__file__', str(self.module_path)), \
                patch.object(user_guide.st, 'image', wraps=user_guide.st.image) as images:
            app = AppTest.from_string(HELP_APP, default_timeout=15).run()
            self.assertFalse(list(app.exception))
            guide = user_guide.load_operation_guide()
            self.assertEqual([item.label for item in app.expander], [title for title, _ in guide.sections])
            self.assertTrue(app.expander[0].proto.expanded)
            for _, content in guide.sections:
                if '![' not in content:
                    self.assertTrue(any(item.value == content for item in app.markdown))
            expected_images = [path.read_bytes() for path in self.pictures]
            self.assertEqual([call.args[0] for call in images.call_args_list], expected_images)
            self.assertEqual({call.kwargs['width'] for call in images.call_args_list}, {1400})
            self.assertEqual(len(app.get('image')), len(expected_images))
            urls = [item.proto.imgs[0].url for item in app.get('image')]
            self.assertFalse(any('![' in item.value for item in app.markdown))
            images.reset_mock()
            app.button(key='switch_help').click().run()
            self.assertFalse(list(app.exception))
            rendered = '\n\n'.join(item.value for item in app.markdown)
            self.assertTrue(all('## ' + title in rendered for title, _ in guide.sections))
            self.assertEqual([call.args[0] for call in images.call_args_list], expected_images)
            self.assertEqual([item.proto.imgs[0].url for item in app.get('image')], urls)
            self.assertFalse(list(app.expander))

            with self.guide_path.open('a', encoding='utf-8') as target:
                target.write('\n\n## 新增使用步骤\n\n这里是交付后修订的使用步骤。\n')
            app.run()
            updated = user_guide.load_operation_guide()
            self.assertTrue(any(updated.sections[-1][1] in item.value for item in app.markdown))
            app.button(key='switch_help').click().run()
            self.assertFalse(list(app.exception))
            self.assertEqual(app.expander[-1].label, updated.sections[-1][0])
            self.assertTrue(any(item.value == updated.sections[-1][1] for item in app.markdown))

    def test_missing_or_invalid_bundled_help_keeps_return_available(self):
        with patch.object(user_guide, '__file__', str(self.module_path)):
            original = self.guide_path.read_text(encoding='utf-8')
            self.guide_path.unlink()
            app = AppTest.from_string(HELP_APP, default_timeout=15).run()
            self.assertFalse(list(app.exception))
            self.assertTrue(list(app.error))
            self.assertFalse(app.button(key='user_guide_home').disabled)
            self.assertFalse(list(app.expander))
            self.guide_path.write_text('# 损坏说明\n\n缺少操作章节。', encoding='utf-8')
            app.button(key='switch_help').click().run()
            self.assertFalse(list(app.exception))
            self.assertTrue(list(app.error))
            self.assertFalse(app.button(key='user_guide_home').disabled)
            # Broken images keep all textual steps usable. Paths outside the
            # document directory, including symlinks, are rejected before read.
            self.guide_path.write_text(original, encoding='utf-8')
            self.pictures[0].write_bytes(b'not a PNG')
            app.run()
            self.assertFalse(list(app.exception))
            self.assertEqual(len(app.get('image')), 2)
            self.assertTrue(any('暂时无法显示' in item.value for item in app.caption))
            self.assertFalse(app.button(key='user_guide_home').disabled)
            self.assertTrue(any('## 首次使用' in item.value for item in app.markdown))
            directory = self.stage / 'docs'
            for reference in ('../secret.png', '/private/tmp/secret.png', 'https://example.com/a.png',
                              'current_user_guide.md'):
                with self.assertRaises(ValueError):
                    user_guide._help_image_path(directory, reference)
            outside = self.stage / 'secret.png'
            outside.write_bytes(b'outside help directory')
            (directory / 'shortcut.png').symlink_to(outside)
            with self.assertRaises(ValueError):
                user_guide._help_image_path(directory, 'shortcut.png')


if __name__ == '__main__':
    unittest.main(verbosity=2)
