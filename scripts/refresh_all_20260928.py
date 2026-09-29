from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'dividend-estimates'))
import build as d

SPREADSHEET = d.r.SPREADSHEET
BASE = f'sheets/v2/spreadsheets/{SPREADSHEET}'
SOURCE = 'vrXy4U'
TITLE = '2026-09-28（全部）'


def api(path, token, method='GET', body=None):
    return d.api(path, token=token, method=method, body=body)


def load_worker():
    path = Path(__file__).resolve().parent / 'refresh_all_20260923.py'
    spec = importlib.util.spec_from_file_location('refresh_worker_0928', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def create_or_get(token):
    meta = api(f'sheets/v3/spreadsheets/{SPREADSHEET}/sheets/query', token)
    existing = next((s for s in meta['data']['sheets'] if s['title'] == TITLE), None)
    if existing:
        return existing['sheet_id'], False
    result = api(
        BASE + '/sheets_batch_update', token, method='POST',
        body={'requests': [{'addSheet': {'properties': {
            'title': TITLE, 'index': len(meta['data']['sheets']),
            'gridProperties': {'rowCount': 500, 'columnCount': 51,
                               'frozenRowCount': 1, 'frozenColumnCount': 2},
        }}}]},
    )
    return result['data']['replies'][0]['addSheet']['properties']['sheetId'], True


def clone_template_values(token, dest):
    values = api(
        f'{BASE}/values/{SOURCE}!A1:AV500?valueRenderOption=UnformattedValue', token
    )['data']['valueRange']['values']
    api(BASE + '/values', token, method='PUT', body={
        'valueRange': {'range': f'{dest}!A1:AV500', 'values': values}
    })
    return values


def clone_condition_formats(token, dest):
    source_cf = api(BASE + f'/condition_formats?sheet_ids={SOURCE}', token)
    batch = []
    for entry in source_cf['data']['sheet_condition_formats']:
        condition = dict(entry['condition_format'])
        condition.pop('cf_id', None)
        condition['ranges'] = [x.replace(SOURCE + '!', dest + '!') for x in condition.get('ranges', [])]
        batch.append({'sheet_id': dest, 'condition_format': condition})
    if batch:
        api(BASE + '/condition_formats/batch_create', token, method='POST', body={
            'sheet_condition_formats': batch
        })
    return source_cf


def main(mode):
    token = d.r.b.get_feishu_token(d.r.b.DEFAULT_FEISHU_ENV)
    dest, created = create_or_get(token)
    clone_template_values(token, dest)
    existing_cf = api(BASE + f'/condition_formats?sheet_ids={dest}', token)
    source_cf = clone_condition_formats(token, dest) if not existing_cf['data']['sheet_condition_formats'] else None

    worker = load_worker()
    worker.SHEET = dest
    worker.ASOF = '20260928'
    worker.ASOF_DISPLAY = '2026-09-28'
    worker.BASE = f'sheets/v2/spreadsheets/{SPREADSHEET}'
    worker.old.ASOF = worker.ASOF
    worker.old.ASOF_DISPLAY = worker.ASOF_DISPLAY
    worker.ROOT = Path(__file__).resolve().parent / 'refresh-all-20260928'
    worker.RAW = worker.ROOT / 'raw'
    worker.OUT = worker.ROOT / 'prepared'
    worker.RAW.mkdir(parents=True, exist_ok=True)
    worker.OUT.mkdir(parents=True, exist_ok=True)
    worker.old.RAW = worker.RAW
    worker.old.OUT = worker.OUT
    worker.old.b.RAW_DIR = worker.RAW
    worker.old.repair.RAW_DIR = worker.RAW
    if mode == 'prepare':
        worker.prepare()
        return
    worker.apply()
    # The worker applies cell-level colors and number formats. Extend the
    # copied data-bar rule to the complete new table when needed.
    audit = json.loads((worker.OUT / 'audit.json').read_text())
    end = len(audit) + 1
    cf = api(BASE + f'/condition_formats?sheet_ids={dest}', token)
    has_extended = any(
        f'{dest}!AT2:AU{end}' in entry['condition_format'].get('ranges', [])
        for entry in cf['data']['sheet_condition_formats']
    )
    if not has_extended and any(
        entry['condition_format'].get('rule_type') == 'dataBar'
        for entry in cf['data']['sheet_condition_formats']
    ):
        source_rule = next(
            entry['condition_format'] for entry in cf['data']['sheet_condition_formats']
            if entry['condition_format'].get('rule_type') == 'dataBar'
        )
        condition = dict(source_rule)
        condition.pop('cf_id', None)
        condition['ranges'] = [f'{dest}!AT2:AU{end}']
        api(BASE + '/condition_formats/batch_create', token, method='POST', body={
            'sheet_condition_formats': [{'sheet_id': dest, 'condition_format': condition}]
        })
    final_cf = api(BASE + f'/condition_formats?sheet_ids={dest}', token)
    verification = json.loads((worker.OUT / 'verification.json').read_text())
    verification['sheet_id'] = dest
    verification['title'] = TITLE
    verification['data_bar_extended'] = True
    (worker.OUT / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2))
    print(json.dumps({'created': created, **verification,
                      'condition_format_rules': len(final_cf['data']['sheet_condition_formats'])},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'prepare')
