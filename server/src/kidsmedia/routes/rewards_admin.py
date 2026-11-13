"""Admin: rewards, i.e. the balance, swaps, wishes, wish requests and the ledger (SPEC §14.4)."""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import Connection, Row, delete, insert, or_, select, update

from kidsmedia.activities import Lang
from kidsmedia.clock import Clock, from_db, to_db
from kidsmedia.db import (
    begin_immediate,
    insert_id,
    reward_ledger,
    reward_swaps,
    reward_wishes,
    rules,
    wish_requests,
)
from kidsmedia.deps import Admin, get_clock, get_conn, require_admin, require_admin_csrf
from kidsmedia.errors import ApiError
from kidsmedia.rewards import balance, earned_stars, spent_stars, swap_suits_rule
from kidsmedia.routes.devices import DEFAULT_PROFILE_ID
from kidsmedia.routes.rewards import RequestStatus, WishTexts, ledger_rows

router = APIRouter(prefix="/api/admin", tags=["admin: rewards"])

LEDGER_ROWS = 50
MAX_STARS = 500


# ---- models -------------------------------------------------------------------------------


class SwapIn(BaseModel):
    stars: int = Field(ge=1, le=MAX_STARS)
    rule_id: int
    extra_items: int = Field(default=0, ge=0, le=20)
    extra_minutes: int = Field(default=0, ge=0, le=600)
    max_per_day: int | None = Field(default=1, ge=1, le=20)  # None = no daily limit
    enabled: bool = True

    @model_validator(mode="after")
    def _one_amount(self) -> "SwapIn":
        if (self.extra_items > 0) == (self.extra_minutes > 0):
            raise ValueError("a swap gives either extra items or extra minutes")
        return self


class SwapPatch(BaseModel):
    stars: int | None = None
    rule_id: int | None = None
    extra_items: int | None = None
    extra_minutes: int | None = None
    max_per_day: int | None = None
    enabled: bool | None = None


class SwapOut(SwapIn):
    id: int


class WishIn(BaseModel):
    texts: dict[Lang, WishTexts] = Field(min_length=1)
    stars: int = Field(ge=1, le=MAX_STARS)
    enabled: bool = True


class WishPatch(BaseModel):
    texts: dict[Lang, WishTexts] | None = None
    stars: int | None = None
    enabled: bool | None = None


class WishOut(WishIn):
    id: int


class RequestOut(BaseModel):
    id: int
    wish_id: int
    status: RequestStatus
    created_at: datetime
    decided_at: datetime | None


class LedgerOut(BaseModel):
    id: int
    delta: int
    kind: Literal["swap", "wish"]
    ref_id: int | None  # reward_swaps.id for a swap, wish_requests.id for a wish
    created_at: datetime


class RewardsAdminOut(BaseModel):
    stars: int
    earned: int
    spent: int
    swaps: list[SwapOut]
    wishes: list[WishOut]
    requests: list[RequestOut]  # pending first, then the decided ones, newest first
    ledger: list[LedgerOut]  # spending rows (swap, wish), newest first, the last 50


# ---- helpers ------------------------------------------------------------------------------


def _merged(model: type[SwapIn] | type[WishIn], current: BaseModel, patch: BaseModel) -> Any:
    """`current` with the fields `patch` sets, validated as `model` (422 like a bad body)."""
    data = current.model_dump(exclude={"id"}) | patch.model_dump(exclude_unset=True)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from exc


def _swap_out(row: Row[Any]) -> SwapOut:
    return SwapOut(
        id=row.id,
        stars=row.stars,
        rule_id=row.rule_id,
        extra_items=row.extra_items,
        extra_minutes=row.extra_minutes,
        max_per_day=row.max_per_day,
        enabled=row.enabled,
    )


def _wish_out(row: Row[Any]) -> WishOut:
    return WishOut(id=row.id, texts=row.texts, stars=row.stars, enabled=row.enabled)


def _load_swap(conn: Connection, swap_id: int) -> SwapOut:
    row = conn.execute(
        select(reward_swaps).where(
            reward_swaps.c.id == swap_id, reward_swaps.c.profile_id == DEFAULT_PROFILE_ID
        )
    ).first()
    if row is None:
        raise ApiError(404, "unknown_swap", swap_id=swap_id)
    return _swap_out(row)


def _load_wish(conn: Connection, wish_id: int) -> WishOut:
    row = conn.execute(
        select(reward_wishes).where(
            reward_wishes.c.id == wish_id, reward_wishes.c.profile_id == DEFAULT_PROFILE_ID
        )
    ).first()
    if row is None:
        raise ApiError(404, "unknown_wish", wish_id=wish_id)
    return _wish_out(row)


def _check_rule(conn: Connection, body: SwapIn) -> None:
    """The rule exists for the profile and the swap gives something under it (§14.4): 422
    unknown_rule, or 422 swap_rule_unsuitable for a `block` rule, extra items on a rule
    without an item limit or extra minutes on one without a minute limit."""
    found = conn.execute(
        select(rules.c.mode, rules.c.max_items, rules.c.max_minutes).where(
            rules.c.id == body.rule_id,
            or_(rules.c.profile_id.is_(None), rules.c.profile_id == DEFAULT_PROFILE_ID),
        )
    ).first()
    if found is None:
        raise ApiError(422, "unknown_rule", rule_id=body.rule_id)
    if not swap_suits_rule(
        found.mode, found.max_items, found.max_minutes, body.extra_items, body.extra_minutes
    ):
        raise ApiError(422, "swap_rule_unsuitable", rule_id=body.rule_id)


def _swap_values(body: SwapIn) -> dict[str, Any]:
    return body.model_dump()


def _wish_values(body: WishIn) -> dict[str, Any]:
    return {
        "texts": {lang: t.model_dump() for lang, t in body.texts.items()},
        "stars": body.stars,
        "enabled": body.enabled,
    }


def admin_state(conn: Connection) -> RewardsAdminOut:
    profile = DEFAULT_PROFILE_ID
    rows = ledger_rows(conn, profile)
    requests = conn.execute(
        select(wish_requests)
        .where(wish_requests.c.profile_id == profile)
        .order_by(wish_requests.c.id.desc())
    ).all()
    pending = [r for r in requests if r.status == "pending"]
    decided = [r for r in requests if r.status != "pending"][:LEDGER_ROWS]
    return RewardsAdminOut(
        stars=balance(rows),
        earned=earned_stars(rows),
        spent=spent_stars(rows),
        swaps=[
            _swap_out(r)
            for r in conn.execute(
                select(reward_swaps)
                .where(reward_swaps.c.profile_id == profile)
                .order_by(reward_swaps.c.stars, reward_swaps.c.id)
            ).all()
        ],
        wishes=[
            _wish_out(r)
            for r in conn.execute(
                select(reward_wishes)
                .where(reward_wishes.c.profile_id == profile)
                .order_by(reward_wishes.c.stars, reward_wishes.c.id)
            ).all()
        ],
        requests=[
            RequestOut(
                id=r.id,
                wish_id=r.wish_id,
                status=r.status,
                created_at=from_db(r.created_at),
                decided_at=from_db(r.decided_at) if r.decided_at else None,
            )
            for r in pending + decided
        ],
        ledger=[
            LedgerOut(
                id=r.id,
                delta=r.delta,
                kind=r.kind,
                ref_id=r.ref_id,
                created_at=from_db(r.created_at),
            )
            for r in conn.execute(
                select(reward_ledger)
                .where(
                    reward_ledger.c.profile_id == profile,
                    reward_ledger.c.kind.in_(("swap", "wish")),  # not earn, bonus or reopen (§17.3)
                )
                .order_by(reward_ledger.c.id.desc())
                .limit(LEDGER_ROWS)
            ).all()
        ],
    )


# ---- routes -------------------------------------------------------------------------------


@router.get("/rewards", response_model=RewardsAdminOut)
def get_rewards(
    _: Admin = Depends(require_admin), conn: Connection = Depends(get_conn, scope="function")
) -> RewardsAdminOut:
    return admin_state(conn)


@router.post("/rewards/swaps", response_model=SwapOut, status_code=201)
def create_swap(
    body: SwapIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> SwapOut:
    _check_rule(conn, body)
    swap_id = insert_id(
        conn.execute(
            insert(reward_swaps).values(
                profile_id=DEFAULT_PROFILE_ID, created_at=to_db(clock.now()), **_swap_values(body)
            )
        )
    )
    return _load_swap(conn, swap_id)


@router.patch("/rewards/swaps/{swap_id}", response_model=SwapOut)
def update_swap(
    swap_id: int,
    body: SwapPatch,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> SwapOut:
    merged: SwapIn = _merged(SwapIn, _load_swap(conn, swap_id), body)
    _check_rule(conn, merged)
    conn.execute(
        update(reward_swaps).where(reward_swaps.c.id == swap_id).values(**_swap_values(merged))
    )
    return _load_swap(conn, swap_id)


@router.delete("/rewards/swaps/{swap_id}", status_code=204)
def delete_swap(
    swap_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> None:
    _load_swap(conn, swap_id)
    conn.execute(delete(reward_swaps).where(reward_swaps.c.id == swap_id))


@router.post("/rewards/wishes", response_model=WishOut, status_code=201)
def create_wish(
    body: WishIn,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> WishOut:
    wish_id = insert_id(
        conn.execute(
            insert(reward_wishes).values(
                profile_id=DEFAULT_PROFILE_ID, created_at=to_db(clock.now()), **_wish_values(body)
            )
        )
    )
    return _load_wish(conn, wish_id)


@router.patch("/rewards/wishes/{wish_id}", response_model=WishOut)
def update_wish(
    wish_id: int,
    body: WishPatch,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> WishOut:
    merged: WishIn = _merged(WishIn, _load_wish(conn, wish_id), body)
    conn.execute(
        update(reward_wishes).where(reward_wishes.c.id == wish_id).values(**_wish_values(merged))
    )
    return _load_wish(conn, wish_id)


@router.delete("/rewards/wishes/{wish_id}", status_code=204)
def delete_wish(
    wish_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
) -> None:
    """Also deletes its requests; ledger rows of approved ones stay (stars stay spent)."""
    _load_wish(conn, wish_id)
    conn.execute(delete(reward_wishes).where(reward_wishes.c.id == wish_id))


def _decide(conn: Connection, request_id: int, status: RequestStatus, now: datetime) -> Row[Any]:
    """Pending -> `status`, as a conditional UPDATE: of two decisions at once only one
    changes the row; the other gets 409 request_decided (404 unknown_request)."""
    changed = conn.execute(
        update(wish_requests)
        .where(
            wish_requests.c.id == request_id,
            wish_requests.c.profile_id == DEFAULT_PROFILE_ID,
            wish_requests.c.status == "pending",
        )
        .values(status=status, decided_at=now)
    ).rowcount
    row = conn.execute(
        select(wish_requests).where(
            wish_requests.c.id == request_id, wish_requests.c.profile_id == DEFAULT_PROFILE_ID
        )
    ).first()
    if row is None:
        raise ApiError(404, "unknown_request", request_id=request_id)
    if changed != 1:
        raise ApiError(409, "request_decided", status=row.status)
    return row


@router.post("/rewards/requests/{request_id}/approve", response_model=RewardsAdminOut)
def approve_request(
    request_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> RewardsAdminOut:
    """Deducts the wish's stars now; 409 not_enough_stars if the balance is too low (the
    request stays pending). Decision, balance check and spending are one atomic step."""
    now = to_db(clock.now())
    begin_immediate(conn)
    req = _decide(conn, request_id, "approved", now)
    wish = _load_wish(conn, req.wish_id)
    stars = balance(ledger_rows(conn, DEFAULT_PROFILE_ID))
    if stars < wish.stars:  # raising rolls the decision back
        raise ApiError(409, "not_enough_stars", stars=stars, needed=wish.stars)
    conn.execute(
        insert(reward_ledger).values(
            profile_id=DEFAULT_PROFILE_ID,
            delta=-wish.stars,
            kind="wish",
            ref_id=request_id,
            created_at=now,
        )
    )
    return admin_state(conn)


@router.post("/rewards/requests/{request_id}/decline", response_model=RewardsAdminOut)
def decline_request(
    request_id: int,
    _: Admin = Depends(require_admin_csrf),
    conn: Connection = Depends(get_conn, scope="function"),
    clock: Clock = Depends(get_clock),
) -> RewardsAdminOut:
    begin_immediate(conn)
    _decide(conn, request_id, "declined", to_db(clock.now()))
    return admin_state(conn)
