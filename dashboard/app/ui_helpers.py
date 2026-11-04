"""Small display helpers shared with Jinja (same rules as the mock's app.js)."""

# Each project gets its own colour (identity only — status keeps red / amber / green). Same list as APP.hue() in the mock.
HUES = [262, 199, 320, 180, 222, 290, 28, 245, 305, 172, 210, 275]
TECH_HUE = {"Laravel": 22, "Flutter": 205, "Flask": 170}


def hue(project_or_id):
    pid = getattr(project_or_id, "id", project_or_id) or 1
    return HUES[(int(pid) - 1) % len(HUES)]


def tech_hue(name):
    return TECH_HUE.get(name, 240)


def initials(name):
    return "".join(w[0] for w in str(name).split()[:2]).upper()
