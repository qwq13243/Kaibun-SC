"""Complete, current script context, without playback URLs or cache bookkeeping."""
from copy import deepcopy


LINE_FIELDS = (
    'id', 'speaker', 'speakers', 'ja', 'zh', 'emotion', 'kind', 'voice_style',
    'nichika_voice_style', 'onstage', 'focus', 'screen_cast', 'actor_order',
    'actor_states', 'body_mood', 'listener_state_persist', 'performance_segments',
)
SCENE_FIELDS = ('index', 'title', 'place', 'memory', 'transition', 'transition_length',
                'line_transitions', 'line_transition_lengths')


def writer_line(line):
    """Keep original text/context; visual production controls never enter writer prompts."""
    result={k:deepcopy(line[k]) for k in LINE_FIELDS if k in line}
    if 'actor_states' in result:
        result['actor_states']={cid:{k:v for k,v in state.items() if k in ('emotion','body_mood')}
                                for cid,state in result['actor_states'].items()}
    if 'performance_segments' in result:
        result['performance_segments']=[{k:deepcopy(v) for k,v in segment.items() if k in ('ja','zh','start','emotion','body_mood','actor_states')}
                                        for segment in result['performance_segments']]
        for segment in result['performance_segments']:
            if 'actor_states' in segment:
                segment['actor_states']={cid:{k:v for k,v in state.items() if k in ('emotion','body_mood')}
                                         for cid,state in segment['actor_states'].items()}
    return result


def scene_context(scene, number):
    return dict(scene_number=number,
                **{k: deepcopy(scene[k]) for k in SCENE_FIELDS if k in scene},
                lines=[writer_line(line) for line in scene['lines']])


def full_script(doc, replacements=None):
    """Never summarize or slice the written dialogue, including user edits."""
    replacements = replacements or {}
    return [scene_context(replacements.get(str(i), scene), i + 1)
            for i, scene in enumerate(doc['scenes'])]
