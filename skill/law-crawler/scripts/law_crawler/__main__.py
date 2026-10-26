"""工程统一入口；独立skill继续使用scripts中的兼容命令。"""
import argparse
import importlib
import sys


COMMANDS = {
    'coverage-matrix': 'coverage_matrix',
    'recover-attachments': 'attachment_recovery',
    'recovery-workflow': 'recovery_workflow',
    'audit-recovery': 'recovery_audit',
    'recover-missing': 'recover_missing_original',
    'pipeline': 'staged_pipeline',
    'bulk-archive': 'bulk_archive',
    'bulk-download-fast': 'bulk_download_fast',
    'bulk-process': 'bulk_process',
    'bulk-report': 'bulk_report',
    'ocr': 'local_ocr',
    'ocr-tables': 'reconstruct_ocr_tables',
    'auto-tables': 'automatic_tables',
    'check-table-translations': 'translation_tables',
    'download': 'download_regulations',
    'import-file': 'import_browser_download',
    'review-body': 'import_body_review',
    'rebuild-markdown': 'rebuild_markdown',
    'prepare': 'prepare_workspace',
    'browser-healthcheck': 'browser_healthcheck',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=COMMANDS, help='采集、导入、核验、重建或工作区准备')
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        parser.print_help()
        return 0 if len(sys.argv) >= 2 else 2
    command = parser.parse_args(sys.argv[1:2]).command
    module = importlib.import_module('law_crawler.' + COMMANDS[command])
    original = sys.argv
    try:
        sys.argv = [original[0] + ' ' + command, *original[2:]]
        return module.main()
    finally:
        sys.argv = original


if __name__ == '__main__':
    raise SystemExit(main())
