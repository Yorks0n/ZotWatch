"""P5C9C opt-in staging delivery sidecar; original latent-auto result is immutable."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

from zotwatch.config.loader import _read_yaml
from zotwatch.abstract_quality import verified_abstract
from zotwatch.optional_llm import LocalConfig, prepare, read_json, save_json, shadow, translate

SIDECAR = 'rss-ai-v1.json'


def integrate(config, source, final, cache_dir):
    """Judge one provider, then translate only retained papers using separate caches."""
    original = final['recommendations']
    if (final['status'] != 'succeeded' or final['command'] != 'watch'
        or [r['work_key'] for r in source['candidates']] != [r['work_key'] for r in original]):
        raise ValueError('not the exact successful Top 20')
    cache_dir = Path(cache_dir)
    quality_cache = cache_dir / ('jev-cache-v1.json' if config.features.shadow.provider == 'jev' else 'quality-llm-cache-v1.json')
    quality = shadow(config, source['candidates'], source['contexts'], quality_cache)
    retained = quality['filtered_recommendations']
    # Never translate body-like source material or expose private representative text.
    translation_papers = [{**r, 'abstract': verified_abstract(r.get('abstract')) or ''} for r in retained]
    translations = translate(config, translation_papers, cache_dir / 'translation-cache-v1.json')
    outputs = []
    for row in translations['papers']:
        successful = row['status'] in {'succeeded', 'cached'}
        outputs.append(dict(work_key=row['work_key'], title_en=row['title_en'], abstract_en=row['abstract_en'],
            title_zh=row['title_zh'] if successful else None,
            abstract_zh=row['abstract_zh'] if successful else None))
    # The public projection contains no decisions, probability, centers, models or context.
    sidecar = dict(schema_name='zotwatch-staging-rss-ai', schema_version=1,
        run_id=final['run_id'], source_final_sha256=sha256(json.dumps(final, ensure_ascii=False,
            sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
        retained_work_keys=[r['work_key'] for r in retained], papers=outputs)
    return sidecar, quality, translations


def run(config_path, result_path, state, destination):
    destination, state = Path(destination), Path(state)
    config = LocalConfig.model_validate(_read_yaml(config_path)) if Path(config_path).is_file() else LocalConfig()
    if not config.features.translation.enabled and (not config.features.shadow.enabled or config.features.shadow.provider == 'none'):
        return dict(status='disabled', requests=0, original_result_unchanged=True)
    final = read_json(result_path)
    if final.get('status') != 'succeeded' or final.get('command') != 'watch' or not final.get('recommendations'):
        return dict(status='no_recommendations', requests=0)
    revision = final['evidence']['interest_model_revision']
    generation = state / 'latent-state-v1' / 'generations' / revision
    source = prepare(result_path, state / 'runs' / f"filter-diagnostic-{final['run_id']}.json",
        state / 'metadata-cache-v1.json', generation / 'generation.json', generation / 'model.json')
    sidecar, quality, translations = integrate(config, source, final, state / 'optional-ai-cache-v1')
    save_json(destination / SIDECAR, sidecar)
    save_json(destination / 'ai' / 'quality-filter.json', quality)
    save_json(destination / 'ai' / 'translations.json', translations)
    summary = dict(status='evaluated', before_count=quality['before_count'], after_count=quality['after_count'],
        fallback_original_top20=quality['fallback_original_top20'], quality=quality['metadata'],
        translation=translations['metadata'], original_result_unchanged=True)
    save_json(destination / 'ai' / 'summary.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args(argv)
    # Fresh operational destination: on any service/preparation error, omit optional
    # projection and let the unchanged publisher consume the original Top 20.
    target = args.destination / SIDECAR
    try:
        if target.exists():
            raise ValueError('stale projection')
        summary = run(args.config, args.result, args.state, args.destination)
    except Exception:
        if target.exists():
            target.unlink()
        summary = dict(status='fallback', reason='OPTIONAL_AI_UNAVAILABLE', original_result_unchanged=True)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
