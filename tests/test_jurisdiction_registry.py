import tempfile
from pathlib import Path
import unittest

from law_crawler.jurisdiction_registry import resolve_jurisdiction, jurisdiction_registry
from tools.install_skill import install, skills_root


class JurisdictionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, text in {
            'Canada': '# Canada\n[x](https://laws.justice.gc.ca/eng/)',
            'EU': '# EU\n[x](https://eur-lex.europa.eu/n-lex/)\n### 1. 德国（Germany / DE）\n[x](https://www.gesetze-im-internet.de/)\n### 2. 法国（France / FR）\n[x](https://www.legifrance.gouv.fr/)\n## Notes\n[x](https://unrelated.example/)'
        }.items():
            path = self.root / 'countries' / name / 'index.md'
            path.parent.mkdir(parents=True)
            path.write_text(text)

    def test_explicit_alias_and_member_selection(self):
        self.assertEqual(resolve_jurisdiction(self.root, '加拿大').name, 'Canada')
        germany = resolve_jurisdiction(self.root, 'DE')
        self.assertEqual(germany.index.parent.name, 'EU')
        self.assertEqual(germany.platforms, ('gesetze-im-internet.de',))
        self.assertEqual(resolve_jurisdiction(self.root, 'EU').countries, ('Germany', 'France'))

    def test_unknown_and_missing_rejected_without_mutation(self):
        before = list(self.root.rglob('*'))
        for name in (None, '', 'Indonesia', '荷兰', '../Canada'):
            with self.assertRaises(ValueError):
                resolve_jurisdiction(self.root, name)
        self.assertEqual(before, list(self.root.rglob('*')))

    def test_index_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            resolve_jurisdiction(self.root, 'Canada', 'countries/EU/index.md')

    def test_symlink_country_not_registered(self):
        external = self.root / 'external'
        external.mkdir()
        (external / 'index.md').write_text('[law](https://example.gov/)')
        (self.root / 'countries/Indonesia').symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            resolve_jurisdiction(self.root, 'Indonesia')

    def test_individual_index_takes_priority(self):
        path = self.root / 'countries/Germany/index.md'
        path.parent.mkdir()
        path.write_text('[law](https://official.germany.example/)')
        self.assertEqual(resolve_jurisdiction(self.root, 'Germany').index, path)

    def test_project_install_paths_and_self_overlap(self):
        self.assertEqual(skills_root('claude', 'project', self.root), self.root / '.claude/skills')
        self.assertEqual(skills_root('codex', 'project', self.root), self.root / '.agents/skills')
        self.assertEqual(skills_root('opencode', 'project', self.root), self.root / '.opencode/skills')
        source = self.root / 'skills/example'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('example')
        for agent in ('codex', 'claude', 'opencode'):
            target = install(agent, 'project', self.root, source=source)
            self.assertEqual((target / 'SKILL.md').read_text(), 'example')
        with self.assertRaises(ValueError):
            install('codex', destination=source.parent, source=source)


if __name__ == '__main__':
    unittest.main()
