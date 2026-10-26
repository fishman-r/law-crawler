"""校验原文下载区域的状态语义，避免把尝试进度当成下载成功。"""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), '前端行为检查需要 Node.js；服务运行不依赖 Node.js')
class DownloadUITests(unittest.TestCase):
    def test_download_states_counts_and_untrusted_reasons(self):
        app = Path(__file__).resolve().parents[1] / 'observer/static/app.js'
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const render = source.slice(source.indexOf('  function renderDownload('), source.indexOf('  function renderSessionList('));
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.textContent = ''; this.attrs = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute(key, value) { this.attrs[key] = value; }
  set innerHTML(_) { throw Error('Untrusted data must not be assigned as HTML'); }
  get content() { return this.textContent + this.children.map(child => child.content || '').join(''); }
}
const elements = new Map();
const context = {
  $: id => { if (!elements.has(id)) elements.set(id, new Element('div')); return elements.get(id); },
  node: (tag, css, value = '') => { const element = new Element(tag); element.textContent = String(value); return element; },
  text: (value, fallback = '—') => value == null || value === '' ? fallback : String(value),
  num: value => Number.isFinite(Number(value)) && value != null ? Math.max(0, Number(value)) : 0,
  count: value => new Intl.NumberFormat('zh-CN').format(value),
  numberFormat: new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }),
  list: value => Array.isArray(value) ? value : [],
  timestamp: value => value || '未记录',
  badge: (value, statuses, override) => { const element = new Element('span'); element.textContent = (override || statuses[value])[0]; return element; },
  notice: (target, warnings) => { target.textContent = Array.isArray(warnings) ? warnings.join(' ') : ''; },
};
vm.createContext(context); vm.runInContext(render + '\nthis.renderDownload = renderDownload;', context);
const read = id => context.$(id).content;
const fixture = { available: true, batch_id: 'id-full', jurisdiction: 'Indonesia', status: 'running', has_gaps: false, total_details: 2466, attempted_details: 2466, successful_files: 3000, failed_file_references: 41, workers: 5, run_workers: 2, active_concurrency_limit: 5, updated_at: '2026-10-22T03:10:00Z',
  operations: {total: 2466, completed: 2400, active: 5, pending: 4, failed: 56, needs_verification: 1, partition_valid: true},
  timing: {elapsed_seconds: 39723, started_at: '2026-10-21T16:07:57Z', ended_at: null, active_download_seconds: null},
  identity_counts: {regulations: 1800, judicial_guidance: 5, identity_pending_details: 650, duplicate_source_records: 11, non_target_details: 0, versions: null, version_pending_regulations: 1800},
  file_counts: {unique_downloaded_files: 3000, body_files: 1200, annex_files: 1600, related_files: 30, role_pending_files: 170, total_references: 3200, downloaded_references: 3159, failed_references: 41},
  failure_reasons: [{reason: '<img src=x onerror=alert(1)> HTTP 403', count: 34}] };
context.renderDownload(fixture);
assert.equal(read('download-status'), '正在下载');
assert(read('download-metrics').includes('下载任务总量2,466'));
assert(read('download-metrics').includes('已下载完成2,400'));
assert(read('download-metrics').includes('下载中5'));
assert(read('download-metrics').includes('待下载4'));
assert(read('download-metrics').includes('11小时 2分钟'));
assert(!read('download-metrics').includes('3,000'));
assert(read('download-progress-label').includes('2,400 / 2,466'));
assert(context.$('download-progress').value < 100, 'attempting all details must not mean completion');
assert(read('download-partition-note').includes('失败或未完整 56'));
assert(read('download-partition-note').includes('状态待核验 1'));
assert(read('download-inventory').includes('法规目录（元数据去重）1,800'));
assert(read('download-inventory').includes('不同原始文件3,000'));
assert(read('download-inventory').includes('正文文件1,200'));
assert(read('download-inventory').includes('附件文件1,600'));
assert(read('download-inventory').includes('已确认版本数待核验'));
assert(read('download-identity-note').includes('不同版本归入同一法规'));
assert(read('download-updated').includes(fixture.updated_at));
assert(read('download-updated').startsWith('下载数据更新时间：'));
assert(read('download-workers').includes('配置并发：5 路'));
assert(read('download-workers').includes('实际活跃下载时长：待核验'));
assert(read('download-failure-reasons').includes('<img src=x onerror=alert(1)> HTTP 403'));
context.renderDownload({...fixture, status: 'paused', operations: {...fixture.operations, active: 0, pending: 9}});
assert.equal(read('download-status'), '已暂停');
assert(read('download-metrics').includes('下载中0'));
context.renderDownload({...fixture, status: 'finished', has_gaps: true, operations: {...fixture.operations, completed: 2409, active: 0, pending: 0, failed: 57, needs_verification: 0}});
assert.equal(read('download-status'), '已结束 · 有缺口');
assert(context.$('download-progress').value < 100);
assert(read('download-state-note').includes('不能视为全部下载成功'));
context.renderDownload({...fixture, status: 'finished', failed_file_references: 0, failure_reasons: [], workers: null, operations: {...fixture.operations, completed: 2466, active: 0, pending: 0, failed: 0, needs_verification: 0}});
assert.equal(read('download-status'), '已结束');
assert.equal(context.$('download-progress').value, 100);
assert(read('download-workers').includes('配置并发：—'));
context.renderDownload({...fixture, status: 'unrecognized', operations: {}, timing: {}, file_counts: {}, identity_counts: {}});
assert.equal(read('download-status'), '状态未知');
assert.equal(read('download-progress-label'), '下载完成进度待核验');
assert(read('download-inventory').includes('法规目录（元数据去重）待核验'));
assert(read('download-metrics').includes('本批次已历时待核验'));
context.renderDownload({...fixture, timing: {elapsed_seconds: 90061}, operations: {...fixture.operations, partition_valid: false}});
assert(read('download-metrics').includes('1天 1小时 1分钟'));
assert(read('download-partition-note').includes('分区核对不一致'));
const census = {...fixture, batch_kind: 'census_oss', batch_id: 'census-current', stale: true,
  operations: {total: 53430, completed: 558, active: 5, pending: 52794, failed: 70, needs_verification: 3, partition_valid: true},
  task_outcomes: {failed: 10, incomplete: 60, no_original: 3}, oss: {unique_files: 663, bytes: 1610612736},
  timing: {elapsed_seconds: 9000, run_elapsed_seconds: 1800}};
context.renderDownload(census);
assert(read('download-scope').includes('census-current'));
assert(read('download-metrics').includes('下载任务总量53,430'));
assert(read('download-metrics').includes('原件及元数据已完成 OSS 归档校验'));
assert(read('download-progress-label').includes('558 / 53,430'));
assert(read('download-outcomes').includes('失败10不完整60未发现原件3'));
assert(read('download-partition-note').includes('未发现原件 3'));
assert.equal(context.$('download-oss').hidden, false);
assert.equal(context.$('download-legacy-breakdown').hidden, true);
assert(read('download-oss-metrics').includes('本批 OSS 原件663'));
assert(read('download-oss-metrics').includes('1.5 GiB'));
assert(read('download-oss-note').includes('不是本轮新增上传量'));
assert(read('download-workers').includes('本轮运行时长：30分钟'));
assert(read('download-updated').includes('数据已过期'));
context.renderDownload({...census, oss: {}});
assert(read('download-oss-metrics').includes('待核验'));
context.renderDownload(fixture);
assert.equal(context.$('download-oss').hidden, true);
assert.equal(context.$('download-legacy-breakdown').hidden, false);
context.renderDownload(null);
assert.equal(read('download-status'), '未接入');
assert(!read('download-metrics').includes('2,466'));
console.log('Download UI state and count checks passed');
'''
        result = subprocess.run([shutil.which('node'), '-e', script, str(app)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
