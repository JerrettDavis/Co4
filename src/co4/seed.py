from sqlalchemy import select
from co4.models import Member, Project, User, Work
from co4.schemas import Policy

def seed(db):
    with db.transaction() as s:
        if s.scalar(select(User)):
            return
        maintainer = User(github_id=1001, login="maintainer")
        contributor = User(github_id=1002, login="contributor")
        backup = User(github_id=1003, login="backup")
        s.add_all([maintainer, contributor, backup]); s.flush()
        p = Project(repository="co4-demo/tiny-library", repository_id=2001, installation_id=3001,
            owner_id=maintainer.id, policy=Policy(access="verified").model_dump(), budget_tokens=2_000_000)
        s.add(p); s.flush()
        for u in [maintainer, contributor, backup]:
            s.add(Member(project_id=p.id, user_id=u.id, role="owner" if u == maintainer else "contributor", verified=True, watching=True))
        examples = [
            (41, "Handle an empty collection without raising an exception", "The average function should return 0 for an empty list.\n\nGiven an empty collection, when average is called, then it returns 0 rather than dividing by zero.", ["bug", "good first issue"], "queued", 2),
            (42, "Accept an iterable when calculating an average", "Support generator input without consuming it twice. Add examples and regression tests.", ["enhancement"], "validation_pending", 1),
            (43, "Document how rounding behaves at a boundary", "Clarify the rounding contract with executable examples.", ["documentation"], "validation_pending", 0),
            (44, "Remove deprecated numeric conversion behavior", "This is a breaking change and is restricted to project maintainers.", ["breaking-change"], "queued", 1),
        ]
        for number, title, body, labels, state, priority in examples:
            s.add(Work(project_id=p.id, number=number, title=title, body=body, labels=labels, state=state, priority=priority))
