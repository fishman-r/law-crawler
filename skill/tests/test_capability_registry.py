"""Read-only capability schema tests; execute on the designated test server."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from law_crawler import capability_registry as cr


class CapabilityRegistryTests(unittest.TestCase):
    def test_aliases_and_index_hosts_are_canonical(self):
        for value, expected in [('dpr','dpr.go.id'),('ma','mahkamahagung.go.id'),('mk','mkri.id'),('jdih.kemendag.go.id','kemendag.go.id'),('peraturan.bpk.go.id','bpk.go.id'),('ojk.go.id-jdih-supplement','ojk.go.id')]:
            with self.subTest(value=value):self.assertEqual(cr.canonical_platform(value),expected)

    def test_source_evidence_matches_actual_files_and_no_verified_claim(self):
        for name in cr.ADAPTERS:
            row=cr.describe_platform(name)
            self.assertEqual(set(row['capabilities']),set(cr.DIMENSIONS))
            self.assertEqual(row['observed_access'],'not_assessed')
            for capability in row['capabilities'].values():
                self.assertNotEqual(capability['status'],'verified')
                for evidence in capability['evidence']:
                    path=cr._ROOT/evidence['path']
                    self.assertTrue(path.is_relative_to(cr._ROOT))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),evidence['sha256'])

    def test_unknown_index_source_does_not_gain_adapter_or_coverage(self):
        row=cr.describe_platform('unregistered.example')
        self.assertFalse(row['registered_adapter']);self.assertIsNone(row['country'])
        self.assertTrue(all(c['status']=='not_supported' for c in row['capabilities'].values()))

    def test_ambiguous_host_is_not_guessed(self):
        adapters={'first':SimpleNamespace(allowed_hosts=('shared.example',)), 'second':SimpleNamespace(allowed_hosts=('shared.example',))}
        with patch.dict(cr.ADAPTERS,adapters,clear=True):
            self.assertEqual(cr.canonical_platform('shared.example'),'shared.example')
            self.assertFalse(cr.describe_platform('shared.example')['registered_adapter'])

    def test_abstract_discovery_is_not_implemented(self):
        from law_crawler.platform_adapters.base import Adapter
        with patch.dict(cr.ADAPTERS, {'unfinished.example': Adapter()}, clear=True):
            row=cr.describe_platform('unfinished.example')
            self.assertEqual(row['capabilities']['discovery']['status'],'not_supported')

    def test_aggregator_member_host_uses_only_unique_declared_entry(self):
        adapters={'member':SimpleNamespace(allowed_hosts=('member.example',),entry_urls=('https://member.example/catalog',)),
                  'aggregator':SimpleNamespace(allowed_hosts=('member.example','index.example'),entry_urls=('https://index.example/',))}
        with patch.dict(cr.ADAPTERS,adapters,clear=True):
            self.assertEqual(cr.canonical_platform('member.example'),'member')
            adapters['aggregator'].entry_urls=('https://member.example/other',)
            self.assertEqual(cr.canonical_platform('member.example'),'member.example')

    def test_single_aggregator_member_does_not_gain_catalogue_adapter(self):
        for host in ('jdih.dephub.go.id', 'jdih.jatengprov.go.id', 'jdih.setneg.go.id'):
            with self.subTest(host=host):
                self.assertEqual(cr.canonical_platform(host), host)
                self.assertFalse(cr.describe_platform(host)['registered_adapter'])

    def test_explicit_justice_alternate_host_remains_supported(self):
        for host in ('laws-lois.justice.gc.ca', 'https://www.laws-lois.justice.gc.ca/eng/'):
            with self.subTest(host=host):
                self.assertEqual(cr.canonical_platform(host), 'laws.justice.gc.ca')
                self.assertTrue(cr.describe_platform(host)['registered_adapter'])

    def test_bulk_method_and_census_do_not_prove_complete_pagination(self):
        trade=cr.describe_platform('kemendag.go.id')
        self.assertEqual(trade['capabilities']['pagination']['status'],'partial')
        self.assertGreaterEqual(len(trade['capabilities']['pagination']['evidence']),2)
        self.assertEqual(trade['capabilities']['versions']['status'],'unknown')
        self.assertEqual(cr.describe_platform('laws.justice.gc.ca')['country'],'Canada')
        self.assertIsNone(cr.describe_platform('laws.justice.gc.ca')['census_route'])

    def test_supplement_and_browser_remain_separate_routes(self):
        central=cr.describe_platform('ojk.go.id');supp=cr.describe_platform('ojk.go.id-jdih-supplement')
        self.assertEqual(supp['source_alias'],'ojk.go.id-jdih-supplement')
        self.assertNotEqual(central['census_route']['symbol'],supp['census_route']['symbol'])
        self.assertEqual(cr.describe_platform('jdih.ojk.go.id')['census_route'],supp['census_route'])
        self.assertIn('browser_supplement',cr.describe_platform('ma'))


if __name__=='__main__':unittest.main()
