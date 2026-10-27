"""P5C9C opt-in staging delivery sidecar; original latent-auto result is immutable."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import traceback

from zotwatch.config.loader import _read_yaml
from zotwatch.abstract_quality import verified_abstract
from zotwatch.optional_llm import LLMError, LocalConfig, prepare, read_json, refine_metadata, save_json, shadow, translate

SIDECAR = 'rss-ai-v1.json'


def integrate(config, source, final, cache_dir):
    """Judge one provider, then translate only retained papers using separate caches."""
    original = final['recommendations']
    if (final['status'] != 'succeeded' or final['command'] != 'watch'
        or [r['work_key'] for r in source['candidates']] != [r['work_key'] for r in original]):
        raise ValueError('not the exact successful Top 20')
    cache_dir = Path(cache_dir)
    quality_cache = cache_dir / ('jev-cache-v1.json' if config.features.shadow.provider == 'jev' else 'quality-llm-cache-v1.json')
    try:
        quality = shadow(config, source['candidates'], source['contexts'], quality_cache)
    except Exception:
        # Quality is optional; its failure must not discard valid public translation inputs.
        fallback = config.model_copy(update={'features': config.features.model_copy(update={
            'shadow': config.features.shadow.model_copy(update={'enabled': False, 'service': None})})})
        quality = shadow(fallback, source['candidates'], {}, quality_cache)
        quality['fallback_original_top20'] = True
        quality['metadata']['warning_code'] = 'AI_QUALITY_UNAVAILABLE'
    retained = quality['filtered_recommendations']
    # Never translate body-like source material or expose private representative text.
    translation_papers = [{**r, 'abstract': verified_abstract(r.get('abstract')) or ''} for r in retained]
    translations = translate(config, translation_papers, cache_dir / 'translation-cache-v1.json', mode=config.output_mode)
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
        retained_work_keys=[r['work_key'] for r in retained], papers=outputs, output_mode=config.output_mode)
    return sidecar, quality, translations


def run(config_path, result_path, state, destination, encoder_cache=None):
    destination, state = Path(destination), Path(state)
    config = LocalConfig.model_validate(_read_yaml(config_path)) if Path(config_path).is_file() else LocalConfig()
    if (not config.features.translation.enabled and config.output_mode != 'english'
        and (not config.features.shadow.enabled or config.features.shadow.provider == 'none')):
        return dict(status='disabled', requests=0, original_result_unchanged=True)
    final = read_json(result_path)
    if final.get('status') != 'succeeded' or final.get('command') != 'watch' or not final.get('recommendations'):
        return dict(status='no_recommendations', requests=0)
    revision = final['evidence']['interest_model_revision']
    generation = state / 'latent-state-v1' / 'generations' / revision
    source = prepare(result_path, state / 'runs' / f"filter-diagnostic-{final['run_id']}.json",
        state / 'metadata-cache-v1.json', generation / 'generation.json', generation / 'model.json')
    active_quality = config.features.shadow.enabled and config.features.shadow.provider != 'none'
    if active_quality or (config.features.translation.enabled and config.output_mode != 'english'):
        from zotwatch.metadata import Cache
        try:
            cache = Cache(state, final['evidence']['workspace_repository_id'])
        except (OSError, ValueError, KeyError, TypeError):
            cache = None
            source.setdefault('preparation_warnings', []).append('AI_METADATA_CACHE_UNAVAILABLE')
        source = refine_metadata(source, cache=cache)
    if (active_quality and config.allow_private_context and
        any(not r.get('quality_skip_reason') and verified_abstract(r.get('abstract')) for r in source['candidates'])):
        try:
            from zotwatch.center_profiles import enrich, load_vectors, NAME
            from zotwatch.interests.lifecycle import Generation
            from zotwatch.interests.center_recall_contract import load_center_model
            snapshot = Generation.model_validate(read_json(generation / 'generation.json'))
            source = enrich(config, source, snapshot, load_center_model(generation / 'model.json'),
                load_vectors(snapshot, generation), encoder_cache, state / NAME)
        except Exception:
            # A quality service failure must not prevent independent translation.
            source['candidates'] = [{**row, 'quality_skip_reason': 'quality_context_unavailable'}
                for row in source['candidates']]
            source.setdefault('preparation_warnings', []).append('AI_QUALITY_CONTEXT_UNAVAILABLE')
    sidecar, quality, translations = integrate(config, source, final, state / 'optional-ai-cache-v1')
    save_json(destination / SIDECAR, sidecar)
    repairs = source.get('metadata_repairs', [])
    warnings = set(source.get('preparation_warnings', []))
    if quality['metadata'].get('warning_code'):
        warnings.add(quality['metadata']['warning_code'])
    if any(r['status'] not in {'succeeded', 'cached', 'disabled', 'english', 'no_verified_abstract',
        'source_text_requires_review'} for r in translations['papers']):
        warnings.add('AI_TRANSLATION_PARTIAL')
    for name, document in [('quality-filter.json', quality), ('translations.json', translations),
        ('metadata-repairs.json', repairs)]:
        try:
            save_json(destination / 'ai' / name, document)
        except (OSError, ValueError):
            warnings.add('AI_DIAGNOSTIC_WRITE_FAILED')
    if any(r.get('cache_write_failed') for r in (quality['metadata'], translations['metadata'])):
        warnings.add('AI_CACHE_WRITE_FAILED')
    summary = dict(status='evaluated', before_count=quality['before_count'], after_count=quality['after_count'],
        fallback_original_top20=quality['fallback_original_top20'], quality=quality['metadata'],
        translation=translations['metadata'], translation_status_counts=translations['status_counts'],
        metadata_repair_counts={status: sum(r['status'] == status for r in repairs)
            for status in ('replaced', 'no_verified_abstract')}, warning_codes=sorted(warnings),
        original_result_unchanged=True)
    try:
        save_json(destination / 'ai' / 'summary.json', summary)
    except (OSError, ValueError):
        summary['warning_codes'] = sorted(warnings | {'AI_DIAGNOSTIC_WRITE_FAILED'})
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--destination', required=True, type=Path)
    parser.add_argument("--encoder-cache", type=Path)
    args = parser.parse_args(argv)
    # Fresh operational destination: on any service/preparation error, omit optional
    # projection and let the unchanged publisher consume the original Top 20.
    target = args.destination / SIDECAR
    try:
        if target.exists():
            target.unlink()
        summary = run(args.config, args.result, args.state, args.destination, args.encoder_cache)
    except Exception as exc:
        if target.exists():
            target.unlink()
        # Only fixed codes cross the log boundary; never exception text or private input.
        codes = {'api_unavailable', 'api_http_error', 'output_budget_exceeded', 'invalid_response',
                 'input_budget_exceeded', 'portrait_identity_mismatch', 'missing_key',
                 'portrait_llm_not_configured', 'insufficient_center_representatives',
                 'candidate_abstract_required_for_neighbors'}
        reason = str(exc) if isinstance(exc, LLMError) and str(exc) in codes else 'OPTIONAL_AI_UNAVAILABLE'
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        summary = dict(status='fallback', reason=reason, error_type=type(exc).__name__,
            failure_location=f'{Path(frame.filename).name}:{frame.lineno}:{frame.name}',
            warning_codes=['AI_DELIVERY_FALLBACK'], original_result_unchanged=True)
    for code in summary.get('warning_codes', []):
        print(f'::warning::{code}')
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
