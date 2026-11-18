"""Recognize script thoughts independently of voice synthesis."""


def is_story_thought(line):
    if line.get('kind') == 'thought':
        return True
    text = (line.get('ja') or '').strip() or (line.get('zh') or '').strip()
    pairs = {'(': ')', '（': '）'}
    if len(text) < 3 or text[0] not in pairs or not text[1:-1].strip():
        return False
    expected = []
    for index, char in enumerate(text):
        if char in pairs:
            expected.append(pairs[char])
        elif char in pairs.values():
            if not expected or char != expected.pop():
                return False
            if not expected:
                return index == len(text) - 1
    return False
