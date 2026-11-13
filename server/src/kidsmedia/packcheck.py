"""Checks for example story packs (SPEC §13.5, §15.2, §15.4, §15.5).

Every example pack, bundled or added by a derived image through EXTRA_PACKS_DIR, must pass:

    python -m kidsmedia.packcheck PATH [PATH ...]

A PATH is a pack folder (with world.json) or a folder of pack folders. Prints one line per
problem and exits with 1 if there is any. The server's tests run the same checks on the
bundled packs; the family image's build runs them on its own packs.
"""

import sys
from pathlib import Path

from pydantic import ValidationError

from kidsmedia import thumbs
from kidsmedia.activities import LANGS, LEVELS, Pack, is_simple_material, pack_warnings

# §15.2 (decision 42): problems per level, and the largest answer for age band 6-7 (easier
# within 10, normal within 20, harder within 100).
PROBLEMS_PER_LEVEL = {"easier": 3, "normal": 4, "harder": 5}
MAX_ANSWER_6_7 = {"easier": 10, "normal": 20, "harder": 100}


def check_pack(folder: Path) -> list[str]:
    """The problems of one pack folder; empty when it passes."""
    try:
        p = Pack.model_validate_json((folder / "world.json").read_bytes())
    except (OSError, ValidationError) as e:
        return [f"world.json: {e}"]
    problems: list[str] = []
    if p.slug != folder.name:
        problems.append(f"slug {p.slug!r} differs from the folder name")
    problems += [f"incomplete: {w}" for w in pack_warnings(p.content())]
    problems += _pictures(folder, p)
    problems += _calc(p)
    problems += _materials(p)
    problems += _writes(p)
    return problems


def _pictures(folder: Path, p: Pack) -> list[str]:
    """A cover and a picture per chapter, each a file next to world.json that decodes."""
    out: list[str] = []
    names = [("cover", p.cover)] + [(f"chapter {i}", c.image) for i, c in enumerate(p.chapters, 1)]
    for where, name in names:
        if name is None:
            out.append(f"{where}: no picture")
            continue
        if "/" in name or ".." in name:
            out.append(f"{where}: picture {name!r} isn't a plain file name")
            continue
        path = folder / name
        if not path.is_file():
            out.append(f"{where}: {name} is missing")
            continue
        try:
            img = thumbs.open_image(path.read_bytes())
        except Exception as e:  # any decoding error counts as a broken picture
            out.append(f"{where}: {name} doesn't decode ({e})")
            continue
        if img.width <= 0 or img.height <= 0:
            out.append(f"{where}: {name} is empty")
    return out


def _calc(p: Pack) -> list[str]:
    """Every calc task: 3 / 4 / 5 problems (easier / normal / harder) in every language, with
    the same answers in the same order across de, en and el (§15.2)."""
    out: list[str] = []
    calc = [(pos, t) for pos, c in enumerate(p.chapters, 1) for t in c.tasks if t.kind == "calc"]
    if not calc:
        out.append("no calc task")
    for pos, task in calc:
        if set(task.texts) != set(LANGS):
            out.append(f"chapter {pos}, calc: not in every language")
            continue
        for level in LEVELS:
            answers = {
                lang: [x.answer for x in (t.problems or {}).get(level, [])]
                for lang, t in task.texts.items()
            }
            where = f"chapter {pos}, calc, {level}"
            if len(answers["en"]) != PROBLEMS_PER_LEVEL[level]:
                out.append(
                    f"{where}: {len(answers['en'])} problems, not {PROBLEMS_PER_LEVEL[level]}"
                )
            if not answers["de"] == answers["en"] == answers["el"]:
                out.append(f"{where}: the answers differ between languages")
            if p.age_band == "6-7" and answers["en"] and max(answers["en"]) > MAX_ANSWER_6_7[level]:
                out.append(f"{where}: an answer above {MAX_ANSWER_6_7[level]}")
    return out


def _materials(p: Pack) -> list[str]:
    """§15.5: paper, pencil, coloured pencils, scissors, glue, in the task's language."""
    out: list[str] = []
    for pos, chapter in enumerate(p.chapters, start=1):
        for task in chapter.tasks:
            for lang, t in task.texts.items():
                odd = [m for m in t.materials if not is_simple_material(lang, m)]
                if odd:
                    out.append(f"chapter {pos}, {task.kind}, {lang}: not simple materials {odd}")
    return out


def _writes(p: Pack) -> list[str]:
    """§15.4: a write task asks for a small story at every level, in every language."""
    out: list[str] = []
    writes = [(pos, t) for pos, c in enumerate(p.chapters, 1) for t in c.tasks if t.kind == "write"]
    if not writes:
        out.append("no write task")
    for pos, task in writes:
        if set(task.texts) != set(LANGS):
            out.append(f"chapter {pos}, write: not in every language")
        for lang, t in task.texts.items():
            for name in ("text", "easier", "harder"):
                if not getattr(t, name).strip():
                    out.append(f"chapter {pos}, write, {lang}: no {name} text")
    return out


def pack_folders(path: Path) -> list[Path]:
    """PATH itself when it holds a world.json, else its subfolders that do."""
    if (path / "world.json").is_file():
        return [path]
    return sorted(p for p in path.iterdir() if (p / "world.json").is_file())


def main(argv: list[str]) -> int:
    if not argv:
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    failed = False
    for arg in argv:
        root = Path(arg)
        folders = pack_folders(root) if root.is_dir() else []
        if not folders:
            print(f"{root}: no pack found")
            failed = True
        for folder in folders:
            problems = check_pack(folder)
            for problem in problems:
                print(f"{folder.name}: {problem}")
            print(f"{folder.name}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
            failed = failed or bool(problems)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
