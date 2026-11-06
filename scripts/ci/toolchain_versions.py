# Toolchain version resolver
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from toolchain_catalog import immutable_reference, index_toolchains, load_toolchain_catalog, validate_toolchain_catalog

LANGUAGES = {'java', 'go', 'cpp'}
STATUSES = {'active', 'candidate', 'planned', 'deprecated', 'retired'}

def load_versions(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'cannot load version registry: {exc}') from exc
    if not isinstance(value, dict):
        raise ValueError('version registry root must be an object')
    return value

def validate_versions(data, catalog):
    errors = []
    if data.get('schema_version') != 1:
        errors.append('schema_version must be 1')
    policy = data.get('selection_policy', {})
    if policy.get('project_consumption_statuses') != ['active']:
        errors.append('project consumption must be active only')
    if policy.get('planned_is_never_runnable') is not True:
        errors.append('planned entries must never be runnable')
    if policy.get('require_exact_version') is not True:
        errors.append('exact version matching is required')
    items = data.get('combinations')
    if not isinstance(items, list) or not items:
        return errors + ['combinations must be a non-empty list']
    errors.extend(validate_toolchain_catalog(catalog))
    catalog_index = index_toolchains(catalog)
    seen = set()
    for i, item in enumerate(items):
        p = f'combinations[{i}]'
        if not isinstance(item, dict):
            errors.append(f'{p} must be an object'); continue
        key = item.get('id')
        if not isinstance(key, str) or not key:
            errors.append(f'{p}.id is required')
        elif key in seen:
            errors.append(f'duplicate version combination id: {key}')
        else:
            seen.add(key)
        if item.get('language') not in LANGUAGES:
            errors.append(f'{p}.language is invalid')
        for field in ('language_version', 'build_tool', 'build_tool_version'):
            if not isinstance(item.get(field), str) or not item.get(field):
                errors.append(f'{p}.{field} is required')
        status = item.get('status')
        if status not in STATUSES:
            errors.append(f'{p}.status is invalid')
        tid = item.get('toolchain_id', '')
        if status in {'active', 'candidate'}:
            if not tid or tid not in catalog_index:
                errors.append(f'{p} runnable combination requires a known toolchain_id')
            elif catalog_index[tid].get('status') != status:
                errors.append(f'{p} status does not match toolchain catalog')
        elif tid:
            errors.append(f'{p} non-runnable combination must not bind a toolchain_id')
    return errors

def resolve_version(data, catalog, language, version, build_tool, build_tool_version, for_verification=False):
    errors = validate_versions(data, catalog)
    if errors:
        raise ValueError('invalid version registry: ' + '; '.join(errors))
    matches = [x for x in data['combinations'] if (x.get('language'), x.get('language_version'), x.get('build_tool'), x.get('build_tool_version')) == (language, version, build_tool, build_tool_version)]
    if len(matches) != 1:
        raise ValueError(f'version selection must match exactly one combination; matches={len(matches)}')
    item = matches[0]
    allowed = {'active', 'candidate'} if for_verification else {'active'}
    if item['status'] not in allowed:
        raise ValueError(f"combination {item['id']} is {item['status']}; allowed statuses: {sorted(allowed)}")
    definition = index_toolchains(catalog).get(item.get('toolchain_id', ''))
    if not definition:
        raise ValueError(f"combination {item['id']} has no implementation")
    ref = immutable_reference(definition) if definition.get('execution_mode') == 'container' and definition.get('digest') else ''
    return {'combination_id': item['id'], 'language': language, 'language_version': version, 'build_tool': build_tool, 'build_tool_version': build_tool_version, 'status': item['status'], 'toolchain_id': definition['id'], 'execution_mode': definition['execution_mode'], 'immutable_reference': ref, 'verification_requires_image_build': definition.get('execution_mode') == 'container' and not definition.get('digest')}

def build_matrix(data, catalog):
    errors = validate_versions(data, catalog)
    if errors:
        raise ValueError('invalid version registry: ' + '; '.join(errors))
    idx = index_toolchains(catalog); rows = []
    for item in data['combinations']:
        if item['status'] not in {'active', 'candidate'}: continue
        tool = idx[item['toolchain_id']]
        rows.append({'combination_id': item['id'], 'language': item['language'], 'language_version': item['language_version'], 'build_tool': item['build_tool'], 'build_tool_version': item['build_tool_version'], 'status': item['status'], 'toolchain_id': tool['id'], 'execution_mode': tool['execution_mode'], 'image': immutable_reference(tool) if tool.get('digest') else '', 'dockerfile': tool.get('dockerfile', ''), 'context': tool.get('context', ''), 'smoke_command': tool.get('smoke_command', ''), 'platforms': tool.get('platforms', [])})
    return {'include': rows}

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--registry', default='ci/toolchain-versions.json'); p.add_argument('--catalog', default='ci/toolchains.json')
    p.add_argument('--matrix', action='store_true'); p.add_argument('--language', choices=sorted(LANGUAGES)); p.add_argument('--version')
    p.add_argument('--build-tool'); p.add_argument('--build-tool-version'); p.add_argument('--for-verification', action='store_true')
    a = p.parse_args()
    try:
        data = load_versions(Path(a.registry)); catalog = load_toolchain_catalog(Path(a.catalog))
        if a.matrix: result = build_matrix(data, catalog)
        else:
            if not all((a.language, a.version, a.build_tool, a.build_tool_version)): p.error('all version selector arguments are required')
            result = resolve_version(data, catalog, a.language, a.version, a.build_tool, a.build_tool_version, a.for_verification)
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr); return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    out = os.environ.get('GITHUB_OUTPUT')
    if a.matrix and out:
        with open(out, 'a', encoding='utf-8') as handle:
            handle.write('matrix=' + json.dumps(result, separators=(',', ':')) + '\n')
            handle.write(f"count={len(result['include'])}\n")
    return 0

if __name__ == '__main__': raise SystemExit(main())
