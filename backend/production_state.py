"""Current production failures; historical diagnostics stay in local files."""
import re


def transient(error):
    import httpx
    if isinstance(error,httpx.TransportError) or isinstance(getattr(error,'__cause__',None),httpx.TransportError):
        return True
    return bool(re.search(r'\b(?:429|500|502|503|504)\b',str(error)))


def clear_production_error(doc):
    if doc.get('failure_stage')=='production':
        doc['error']=None
        doc.pop('failure_stage',None)


def story_work(doc, *, busy=False, exporting=False):
    progress = doc.get('production') or {}
    voice_job = (doc.get('voice_job') or {}).get('status')
    status = doc['status']
    scenes = doc.get('scenes') or []
    outline = doc.get('outline')
    complete = bool(outline and len(scenes) >= len(outline['scenes']) and not doc.get('continuation'))
    writing = 'complete' if complete else 'writing' if status in ('queued', 'generating') else 'paused'
    if status == 'failed' and not complete:
        writing = 'failed'
    voices = ('working' if progress.get('voices_active') or voice_job == 'working' else
              'failed' if progress.get('voice_error') or voice_job == 'failed' else
              'complete' if 'voices_total' in progress and progress.get('voices_done', 0) >= progress['voices_total'] else 'pending')
    staging = ('working' if progress.get('staging_active') else 'failed' if progress.get('staging_error') else
               'complete' if progress.get('staging_done') else 'pending')
    editable = (not busy and not exporting and not doc.get('revision_proposal') and
                status in ('ready', 'failed', 'interrupted'))
    return dict(writing=writing, voices=voices, staging=staging, actions=dict(
        pause_writing=writing == 'writing', resume=status in ('failed', 'interrupted') and (not complete or not busy),
        edit=editable and bool(scenes), continue_story=editable and complete,
        export=status == 'ready' and not busy and not doc.get('revision_proposal')))


def normalize_recovered(doc):
    p=doc.get('production') or {}
    covered=isinstance(doc.get('production'),dict) and 'voices_done' in p and 'voices_total' in p
    if covered and p['voices_done']>=p['voices_total']:
        p.pop('voice_error',None)
        p.pop('voice_failed_ids',None)
        p['voices_failed']=0
        job=doc.get('voice_job') or {}
        if job.get('status') in ('failed','interrupted') and job.get('mode') != 'current':
            job.update(status='done',failed=0,message='配音已完成')
            job.pop('last_error',None)
            job['errors']=[]
    if p.get('staging_done'):
        p.pop('staging_error',None)
        p['staging_retrying']=False
    if doc.get('status')=='ready' or (covered and p.get('staging_done') and p['voices_done']>=p['voices_total']):
        clear_production_error(doc)
        if doc.get('failure_stage') is None and (doc.get('revision_progress') or {}).get('status') not in ('failed','interrupted'):
            doc['error']=None
        writing_pending=bool(doc.get('writing_draft')) or str(doc.get('failure_stage') or '').startswith(('scene-','outline'))
        if doc.get('status')=='ready' or not writing_pending:
            doc.pop('writing_notice',None)
            doc.pop('writing_failure',None)
