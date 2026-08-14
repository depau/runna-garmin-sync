"""CLI: login (seed Garmin tokens interactively), dump, sync, daemon, garmin-workout."""

import json
import logging
import os
import sys
import time
from pathlib import Path

import click

from .mapping import Mapping
from .runna import RunnaClient
from .state import State

log = logging.getLogger("runna_garmin_sync")

DEFAULT_STATE_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "runna-garmin-sync"


def _prompt_creds(ctx: click.Context, service: str) -> None:
    for key in (f"{service}_email", f"{service}_password"):
        if not ctx.obj[key]:
            ctx.obj[key] = click.prompt(key.replace("_", " "), hide_input=key.endswith("password"))


def _runna(ctx: click.Context) -> RunnaClient:
    # Credentials may be None: RunnaClient only needs them when there is no
    # valid cached session and the refresh token no longer works.
    return RunnaClient(ctx.obj["runna_email"], ctx.obj["runna_password"], ctx.obj["state"])


def _garmin(ctx: click.Context, interactive: bool = False):
    from garminconnect import GarminConnectAuthenticationError

    from .sync import make_garmin

    o = ctx.obj
    try:
        # Works credential-less when the tokenstore holds a valid session.
        return make_garmin(o["garmin_email"], o["garmin_password"], o["state"], interactive)
    except GarminConnectAuthenticationError as e:
        if not interactive:
            raise click.ClickException(f"no valid Garmin session ({e}); run `runna-garmin-sync login` first") from e
        _prompt_creds(ctx, "garmin")
        return make_garmin(o["garmin_email"], o["garmin_password"], o["state"], interactive)


@click.group()
@click.option("--state-dir", envvar="STATE_DIR", default=str(DEFAULT_STATE_DIR), show_default=True)
@click.option("--runna-email", envvar="RUNNA_EMAIL")
@click.option("--runna-password", envvar="RUNNA_PASSWORD")
@click.option("--garmin-email", envvar="GARMIN_EMAIL")
@click.option("--garmin-password", envvar="GARMIN_PASSWORD")
@click.option("--mapping-csv", envvar="MAPPING_CSV", default=None, help="override the bundled exercise mapping CSV")
@click.pass_context
def cli(ctx, state_dir, runna_email, runna_password, garmin_email, garmin_password, mapping_csv):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctx.obj = {
        "state": State(state_dir),
        "runna_email": runna_email,
        "runna_password": runna_password,
        "garmin_email": garmin_email,
        "garmin_password": garmin_password,
        "mapping_csv": mapping_csv,
    }


@cli.command()
@click.pass_context
def login(ctx):
    """Interactive first-time login: Garmin (with MFA prompt) + Runna. Prompts
    for credentials only when there is no reusable session."""
    from .runna import RunnaError

    garmin = _garmin(ctx, interactive=True)
    click.echo(f"Garmin: logged in as {garmin.display_name}; tokens saved to {ctx.obj['state'].path('garmin_tokens')}")
    try:
        url = _runna(ctx).ical_url()
    except RunnaError:
        _prompt_creds(ctx, "runna")
        url = _runna(ctx).ical_url()
    click.echo(f"Runna: authenticated; calendar {url}")


@cli.command()
@click.option("--no-cache", is_flag=True, help="refetch from Runna even if the calendar is unchanged")
@click.pass_context
def dump(ctx, no_cache):
    """Print all Runna strength days as JSON (cached; ETag-invalidated)."""
    json.dump(_runna(ctx).strength_days_cached(refresh=no_cache), sys.stdout, indent=2, ensure_ascii=False)


@cli.command()
@click.pass_context
def push(ctx):
    """Push all synced upcoming workouts to the primary training device."""
    import datetime

    from .sync import SYNC_FILE, push_to_device

    tracked = ctx.obj["state"].load(SYNC_FILE, {}).get("workouts", {})
    today = datetime.date.today().isoformat()
    ids = [r["garminWorkoutId"] for r in tracked.values() if r["date"] >= today]
    if not ids:
        click.echo("nothing synced yet — run `sync` first")
        return
    pushed = push_to_device(_garmin(ctx), ids)
    click.echo(f"pushed {pushed}/{len(ids)} workouts to the primary training device")


@cli.command()
@click.option("--yes", is_flag=True, help="skip the confirmation prompt")
@click.pass_context
def purge(ctx, yes):
    """Delete ALL Garmin workouts created by this tool (incl. past ones)."""
    from .sync import SYNC_FILE, delete_all

    tracked = ctx.obj["state"].load(SYNC_FILE, {}).get("workouts", {})
    if not tracked:
        click.echo("nothing tracked — nothing to delete")
        return
    if not yes:
        click.confirm(f"Delete {len(tracked)} workout(s) from Garmin Connect?", abort=True)
    deleted = delete_all(_garmin(ctx), ctx.obj["state"])
    click.echo(f"deleted {deleted}/{len(tracked)} workouts")


@cli.command("garmin-workout")
@click.argument("workout_id")
@click.pass_context
def garmin_workout(ctx, workout_id):
    """Dump a Garmin workout JSON by id (debug: inspect UI-created workouts, e.g. Reps+)."""
    json.dump(_garmin(ctx).get_workout_by_id(workout_id), sys.stdout, indent=2)


def _do_sync(ctx: click.Context, refresh: bool = False) -> dict:
    from .sync import full_sync

    mapping = Mapping(ctx.obj["state"], ctx.obj["mapping_csv"])
    return full_sync(_runna(ctx), _garmin(ctx), mapping, ctx.obj["state"], refresh=refresh)


def _notify(url: str | None, title: str, body: str) -> None:
    if not url:
        return
    try:
        import apprise

        a = apprise.Apprise()
        a.add(url)
        a.notify(title=title, body=body, body_format=apprise.NotifyFormat.MARKDOWN)
    except Exception:
        log.exception("notification failed")


def _sync_notification(stats: dict) -> str:
    lines = []
    for c in stats["changes"]:
        label = f"[{c['name']}]({c['link']})" if c.get("link") else c["name"]
        lines.append(f"- {label} — {c['action']}, {c['date']}")
    counts = ", ".join(f"{k} {v}" for k, v in stats.items() if isinstance(v, int) and v and k != "unchanged")
    return "\n".join([counts, *lines])


@cli.command()
@click.option("--dry-run", "-n", is_flag=True, help="don't touch Garmin; show what a sync would do")
@click.option("--json", "as_json", is_flag=True, help="with --dry-run: emit the plan (incl. Garmin DTOs) as JSON")
@click.option("--no-cache", is_flag=True, help="refetch from Runna even if the calendar is unchanged")
@click.pass_context
def sync(ctx, dry_run, as_json, no_cache):
    """One-shot full sync."""
    if not dry_run:
        _do_sync(ctx, refresh=no_cache)
        return
    from .builder import describe_workout
    from .sync import plan_sync

    mapping = Mapping(ctx.obj["state"], ctx.obj["mapping_csv"])
    plan = plan_sync(_runna(ctx), mapping, ctx.obj["state"], refresh=no_cache)
    if as_json:
        json.dump(plan, sys.stdout, indent=2, ensure_ascii=False)
        return
    for item in plan:
        click.echo(f"== {item['action'].upper()} {item['date']} ({item['runnaId']})")
        if item.get("workout") and item["action"] != "unchanged":
            click.echo(describe_workout(item["workout"]))
        click.echo()


@cli.command()
@click.option(
    "--poll-interval",
    envvar="POLL_INTERVAL",
    default=60,
    show_default=True,
    help="seconds between calendar ETag polls",
)
@click.option(
    "--force-sync-hours",
    envvar="FORCE_SYNC_HOURS",
    default=6.0,
    show_default=True,
    help="full sync even without a calendar change",
)
@click.option(
    "--notify-url",
    envvar="NOTIFY_URL",
    default=None,
    help="Apprise URL for sync notifications (see https://github.com/caronc/apprise)",
)
@click.option(
    "--notify-error-url",
    envvar="NOTIFY_ERROR_URL",
    default=None,
    help="Apprise URL for error notifications (defaults to --notify-url)",
)
@click.pass_context
def daemon(ctx, poll_interval, force_sync_hours, notify_url, notify_error_url):
    """Poll the Runna calendar and sync on change."""
    from .runna import CACHE_FILE

    state = ctx.obj["state"]
    runna = _runna(ctx)
    ical = runna.ical_url()
    last_forced = 0.0
    last_error = None
    while True:
        try:
            # read-only peek at the plan cache's ETag; the sync refreshes it
            changed, _ = runna.ical_changed(ical, state.load(CACHE_FILE, {}).get("etag"))
            force = time.monotonic() - last_forced > force_sync_hours * 3600
            if changed or force:
                log.info("syncing (%s)", "calendar changed" if changed else "periodic refresh")
                stats = _do_sync(ctx, refresh=force)
                last_forced = time.monotonic()
                if stats["changes"]:
                    _notify(notify_url, "Runna plan synced to Garmin", _sync_notification(stats))
            last_error = None
        except Exception as e:
            log.exception("sync failed; retrying next poll")
            msg = f"{type(e).__name__}: {e}"
            if msg != last_error:  # don't re-notify the same failure every poll
                _notify(notify_error_url or notify_url, "Runna→Garmin sync error", msg)
                last_error = msg
        time.sleep(poll_interval)


if __name__ == "__main__":
    cli()
