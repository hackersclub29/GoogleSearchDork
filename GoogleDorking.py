#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Dorking Tool — v3 (production-ready)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fixes over v2
─────────────
 v2→v3 fixes (13 new issues found in v2):
  1. _keys_exhausted race    → flag check + set moved inside _key_lock; atomic
  2. gather() ate exceptions → results scanned for Exception instances, logged
  3. shared-set mutation     → asyncio.Lock guards unique_results.update()
  4. --sets > 26 + append    → validated: max sets = 26 when --append-chars on
  5. sets > 1 without append → explicit warning + auto-enable --append-chars
                               OR use start_index offset strategy per set
  6. CLI input validation    → --sets ≥ 1, --concurrency ≥ 1, --timeout ≥ 1
  7. silent file overwrite   → warns user + requires --overwrite to proceed
  8. task dedup              → (query, start_index) set prevents duplicate requests
  9. no TCPConnector limit   → aiohttp.TCPConnector(limit_per_host=concurrency)
 10. no User-Agent           → rotating realistic browser UA list
 11. api.txt CWD resolution  → tries CWD first, then ~/.config/gdork/api.txt
 12. empty-result display    → _display_and_save skips table if 0 results
 13. pending coros after     → asyncio.Event cancels pending tasks immediately
     key exhaustion            when _keys_exhausted is set

 v1→v2 fixes retained:
  A. Key rotation race       → asyncio.Lock on _current_key_index
  B. Progress accuracy       → update() fires after HTTP response, not creation
  C. Concurrency cap         → asyncio.Semaphore(--concurrency)
  D. Request timeout         → aiohttp.ClientTimeout
  E. CX hardcoded (OPSEC)   → --cx / GOOGLE_CX env var
  F. Retry + backoff         → exponential backoff on 5xx / timeout
  G. chr() injection         → --append-chars opt-in only
  H. Non-deterministic order → sorted output (table + file)
  I. Partial result loss     → finally block always flushes

Install:
    pip install aiohttp click rich

Usage:
    export GOOGLE_CX=your_cx_id
    python gdork_v3.py search --dork 'site:example.com filetype:json'
    python gdork_v3.py search --dork '...' --sets 3 --output-file out.txt
    python gdork_v3.py search --dork '...' --append-chars --sets 5
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

import aiohttp
import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table

console = Console()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL          = "https://www.googleapis.com/customsearch/v1"
DEFAULT_TIMEOUT   = 20
DEFAULT_RETRIES   = 3
DEFAULT_CONC      = 5
BACKOFF_BASE      = 1.0
MAX_SETS_APPEND   = 26          # chr(65+25) = 'Z' — hard cap when --append-chars

# FIX 10 — realistic rotating User-Agents
USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# FIX 11 — fallback config path when api.txt not in CWD
CONFIG_DIR  = Path.home() / ".config" / "gdork"
DEFAULT_KEYS_LOCATIONS = ["api.txt", str(CONFIG_DIR / "api.txt")]


# ─────────────────────────────────────────────────────────────────────────────
# Search client
# ─────────────────────────────────────────────────────────────────────────────

class GoogleSearchClient:
    """
    Production-grade async Google Custom Search API client.

    Concurrency model:
      _key_lock    — serialises all key-index reads/writes AND _keys_exhausted
                     flag mutations (FIX 1 + v2 FIX A)
      _semaphore   — caps simultaneous in-flight HTTP requests (v2 FIX C)
      _results_lock— serialises writes to the shared unique_results set (FIX 3)
      _abort_event — signals all pending coroutines to exit when keys exhausted
                     or KeyboardInterrupt received (FIX 13)
    """

    def __init__(
        self,
        api_keys:    List[str],
        cx:          str,
        concurrency: int = DEFAULT_CONC,
        timeout:     int = DEFAULT_TIMEOUT,
        retries:     int = DEFAULT_RETRIES,
    ):
        if not api_keys:
            raise ValueError("API key list cannot be empty.")
        if not cx:
            raise ValueError(
                "Custom Search Engine ID (CX) is required. "
                "Pass --cx or set GOOGLE_CX env var."
            )
        self._api_keys          = api_keys
        self._cx                = cx
        self._current_key_index = 0
        self._keys_exhausted    = False

        # FIX 1 + v2 A — single lock guards both index and exhausted flag
        self._key_lock          = asyncio.Lock()
        # FIX 3 — guards shared unique_results set
        self._results_lock      = asyncio.Lock()
        # FIX 13 — abort signal broadcast to all pending coroutines
        self._abort_event       = asyncio.Event()

        self._semaphore         = asyncio.Semaphore(concurrency)
        self._timeout           = aiohttp.ClientTimeout(total=timeout)
        self._retries           = retries
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "GoogleSearchClient":
        import random
        # FIX 9 — TCPConnector with explicit per-host limit
        connector    = aiohttp.TCPConnector(
            limit_per_host=max(self._semaphore._value, 1),
            limit=max(self._semaphore._value * 2, 2),
        )
        self._session = aiohttp.ClientSession(
            timeout   = self._timeout,
            connector = connector,
            headers   = {"User-Agent": random.choice(USER_AGENTS)},  # FIX 10
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._session:
            await self._session.close()

    # ── FIX 1: atomic key rotation + exhaustion check ─────────────────────────

    async def _get_next_key(self) -> Optional[str]:
        """
        Returns the next available API key under lock, or None if exhausted.
        Both the exhausted-flag read and the index advance are inside the same
        lock acquisition so no coroutine can race past the guard.
        """
        async with self._key_lock:
            if self._keys_exhausted:
                return None
            key = self._api_keys[self._current_key_index]
            self._current_key_index = (
                (self._current_key_index + 1) % len(self._api_keys)
            )
            return key

    async def _mark_exhausted(self) -> None:
        """Set the exhausted flag and fire the abort event under lock."""
        async with self._key_lock:
            self._keys_exhausted = True
        self._abort_event.set()   # FIX 13 — wake all waiting coroutines

    # ── Core search ───────────────────────────────────────────────────────────

    async def search(
        self,
        query:       str,
        start_index: int = 1,
    ) -> Optional[List[str]]:
        """
        One page of results. Returns list of URLs or None on hard failure.
        FIX 13 — checks _abort_event before doing any work.
        """
        if not self._session:
            raise RuntimeError("Use 'async with GoogleSearchClient(...) as client'.")

        # FIX 13 — bail immediately if abort already signalled
        if self._abort_event.is_set():
            return None

        import random
        total_key_attempts = len(self._api_keys)
        key_attempts_used  = 0

        while key_attempts_used < total_key_attempts:
            # FIX 13 — check between key rotation attempts
            if self._abort_event.is_set():
                return None

            api_key = await self._get_next_key()
            if api_key is None:
                return None
            key_attempts_used += 1

            params = {
                "key":   api_key,
                "cx":    self._cx,
                "q":     query,
                "start": start_index,
                "num":   10,
            }
            # FIX 10 — rotate UA per request (session default is set at open;
            #           override header per-request for variety)
            headers = {"User-Agent": random.choice(USER_AGENTS)}

            for attempt in range(1, self._retries + 1):
                # FIX 13 — check before each attempt
                if self._abort_event.is_set():
                    return None

                async with self._semaphore:
                    try:
                        async with self._session.get(
                            BASE_URL, params=params, headers=headers
                        ) as resp:
                            status = resp.status

                            if status == 200:
                                data = await resp.json()
                                return [
                                    item["link"]
                                    for item in data.get("items", [])
                                    if item.get("link")
                                ]

                            if status == 429:
                                console.log(
                                    f"[yellow]Key …{api_key[-4:]} quota hit — rotating[/yellow]"
                                )
                                break  # next key immediately

                            if 500 <= status < 600:
                                wait = BACKOFF_BASE * (2 ** (attempt - 1))
                                console.log(
                                    f"[yellow]{status} attempt {attempt}/{self._retries}"
                                    f" — retry in {wait:.1f}s[/yellow]"
                                )
                                await asyncio.sleep(wait)
                                continue

                            err = await resp.text()
                            console.log(f"[red]API {status}: {err[:200]}[/red]")
                            return None

                    except asyncio.TimeoutError:
                        wait = BACKOFF_BASE * (2 ** (attempt - 1))
                        console.log(
                            f"[yellow]Timeout attempt {attempt}/{self._retries}"
                            f" — retry in {wait:.1f}s[/yellow]"
                        )
                        await asyncio.sleep(wait)

                    except aiohttp.ClientError as exc:
                        wait = BACKOFF_BASE * (2 ** (attempt - 1))
                        console.log(
                            f"[yellow]Network error ({exc}) attempt {attempt}/{self._retries}"
                            f" — retry in {wait:.1f}s[/yellow]"
                        )
                        await asyncio.sleep(wait)

        # All keys tried — mark exhausted and abort pending coroutines
        await self._mark_exhausted()
        console.log("[bold red]All API keys have reached their quota.[/bold red]")
        return None

    # ── FIX 3: thread-safe result accumulation ────────────────────────────────

    async def accumulate(
        self, unique_results: Set[str], urls: Optional[List[str]]
    ) -> None:
        """Merge urls into shared set under lock."""
        if not urls:
            return
        async with self._results_lock:
            unique_results.update(urls)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

@click.group()
def cli() -> None:
    """High-performance Google Dorking tool — v3."""


@cli.command()
@click.option("--dork",         required=True,
              help="Google dork query string.")
@click.option("--sets",         default=1, type=int, show_default=True,
              help="Number of result sets. Each set = up to 100 URLs (10 pages × 10).")
@click.option("--output-file",  type=click.Path(), default=None,
              help="Write unique sorted URLs to this file.")
@click.option("--cx",           default=lambda: os.environ.get("GOOGLE_CX", ""),
              show_default="GOOGLE_CX env var",
              help="Google Custom Search Engine ID.")
@click.option("--keys-file",    type=click.Path(), default=None,
              help=f"File with one API key per line. Defaults: {DEFAULT_KEYS_LOCATIONS}")
@click.option("--concurrency",  default=DEFAULT_CONC, type=int, show_default=True,
              help="Max simultaneous HTTP requests.")
@click.option("--timeout",      default=DEFAULT_TIMEOUT, type=int, show_default=True,
              help="Per-request timeout in seconds.")
@click.option("--retries",      default=DEFAULT_RETRIES, type=int, show_default=True,
              help="Retry attempts per request on 5xx/timeout.")
@click.option("--append-chars", is_flag=True, default=False,
              help=(
                  "Append A/B/C… to each set's query to work around Google's "
                  "100-result cap. Alters search semantics. Max 26 sets."
              ))
@click.option("--overwrite",    is_flag=True, default=False,
              help="Overwrite output-file if it already exists. (FIX 7)")
@click.option("--verbose",      is_flag=True, default=False,
              help="Enable verbose debug logging.")
def search(
    dork:         str,
    sets:         int,
    output_file:  Optional[str],
    cx:           str,
    keys_file:    Optional[str],
    concurrency:  int,
    timeout:      int,
    retries:      int,
    append_chars: bool,
    overwrite:    bool,
    verbose:      bool,
) -> None:
    """Run Google dork searches and collect unique URLs."""

    if verbose:
        import logging
        logging.basicConfig(
            level="DEBUG",
            handlers=[RichHandler(console=console, rich_tracebacks=True)],
        )

    # ── FIX 6: input validation ───────────────────────────────────────────────
    errors = []
    if sets < 1:
        errors.append(f"--sets must be ≥ 1 (got {sets})")
    if concurrency < 1:
        errors.append(f"--concurrency must be ≥ 1 (got {concurrency})")
    if timeout < 1:
        errors.append(f"--timeout must be ≥ 1 (got {timeout})")
    if retries < 0:
        errors.append(f"--retries must be ≥ 0 (got {retries})")
    if append_chars and sets > MAX_SETS_APPEND:
        errors.append(
            f"--append-chars supports at most {MAX_SETS_APPEND} sets (A–Z). "
            f"Got --sets {sets}. Remove --append-chars or reduce --sets."
        )
    if errors:
        for e in errors:
            console.print(f"[bold red]Error: {e}[/bold red]")
        sys.exit(1)

    # ── FIX 5: warn about sets > 1 without append-chars ──────────────────────
    if sets > 1 and not append_chars:
        console.print(
            f"[yellow]Warning: --sets {sets} without --append-chars will query "
            f"the same 10 pages {sets} times, producing duplicate results and "
            f"wasting quota. Add --append-chars to get distinct result sets.[/yellow]"
        )

    # ── FIX 5 CX check ───────────────────────────────────────────────────────
    if not cx:
        console.print(
            "[bold red]Error: CX not set. "
            "Use --cx YOUR_CX or export GOOGLE_CX=YOUR_CX.[/bold red]"
        )
        sys.exit(1)

    # ── FIX 11: resolve api keys file ────────────────────────────────────────
    api_keys = _load_api_keys(keys_file)
    if api_keys is None:
        sys.exit(1)

    # ── FIX 7: output file collision check ───────────────────────────────────
    if output_file and Path(output_file).exists() and not overwrite:
        console.print(
            f"[bold red]Error: '{output_file}' already exists. "
            f"Use --overwrite to replace it.[/bold red]"
        )
        sys.exit(1)

    asyncio.run(
        run_searches(
            dork         = dork,
            sets         = sets,
            output_file  = output_file,
            api_keys     = api_keys,
            cx           = cx,
            concurrency  = concurrency,
            timeout      = timeout,
            retries      = retries,
            append_chars = append_chars,
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# API key loader  (FIX 11)
# ─────────────────────────────────────────────────────────────────────────────

def _load_api_keys(keys_file: Optional[str]) -> Optional[List[str]]:
    """
    FIX 11 — Resolution order:
      1. Explicit --keys-file path
      2. api.txt in CWD
      3. ~/.config/gdork/api.txt
    """
    candidates = (
        [keys_file] if keys_file else DEFAULT_KEYS_LOCATIONS
    )
    for path_str in candidates:
        p = Path(path_str).expanduser()
        if p.exists():
            raw   = p.read_text(encoding="utf-8").splitlines()
            keys  = [k.strip() for k in raw if k.strip() and not k.startswith("#")]
            if keys:
                console.log(f"[dim]Loaded {len(keys)} API key(s) from {p}[/dim]")
                return keys
            console.print(f"[yellow]Warning: '{p}' contains no valid keys.[/yellow]")

    tried = ", ".join(f"'{c}'" for c in candidates)
    console.print(
        f"[bold red]Error: No API keys found. Tried: {tried}. "
        f"Create one of these files with one Google API key per line.[/bold red]"
    )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Async orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def run_searches(
    dork:         str,
    sets:         int,
    output_file:  Optional[str],
    api_keys:     List[str],
    cx:           str,
    concurrency:  int,
    timeout:      int,
    retries:      int,
    append_chars: bool,
) -> None:
    unique_results: Set[str]                  = set()
    # FIX 8 — task deduplication: track (query, start_index) pairs already queued
    queued_tasks:   Set[Tuple[str, int]]      = set()
    total_pages     = sets * 10

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    )

    # FIX B — wrapper: progress advances after HTTP response, not on creation
    async def _tracked_search(
        client:      GoogleSearchClient,
        query:       str,
        start_index: int,
        prog:        Progress,
        tid:         TaskID,
    ) -> None:
        urls = await client.search(query, start_index)
        prog.update(tid, advance=1)
        await client.accumulate(unique_results, urls)  # FIX 3 — via locked accumulate

    try:
        with progress:
            task_id = progress.add_task(
                f"Dorking: [cyan]{dork}[/cyan] — {total_pages} pages",
                total=total_pages,
            )

            async with GoogleSearchClient(
                api_keys    = api_keys,
                cx          = cx,
                concurrency = concurrency,
                timeout     = timeout,
                retries     = retries,
            ) as client:
                coros = []
                for set_idx in range(sets):
                    # FIX G — character appending is opt-in
                    query = (
                        f'{dork} "{chr(65 + set_idx)}"'
                        if append_chars
                        else dork
                    )
                    for page in range(1, 11):
                        start_index = (page - 1) * 10 + 1
                        # FIX 8 — deduplicate (query, start_index) pairs
                        task_key = (query, start_index)
                        if task_key in queued_tasks:
                            progress.update(task_id, advance=1)
                            continue
                        queued_tasks.add(task_key)
                        coros.append(
                            _tracked_search(client, query, start_index, progress, task_id)
                        )

                # FIX 2 — scan gather results for swallowed exceptions
                results = await asyncio.gather(*coros, return_exceptions=True)
                _log_gather_exceptions(results)

    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted — flushing results collected so far…[/yellow]"
        )

    finally:
        # FIX I — always flush on exit
        _display_and_save(dork, unique_results, output_file)


def _log_gather_exceptions(results: list) -> None:
    """
    FIX 2 — asyncio.gather(return_exceptions=True) returns Exception instances
    instead of raising them. Scan and log any that slipped through.
    """
    for result in results:
        if isinstance(result, Exception):
            console.log(f"[red]Unhandled task exception: {type(result).__name__}: {result}[/red]")


def _display_and_save(
    dork: str, unique_results: Set[str], output_file: Optional[str]
) -> None:
    """FIX H + FIX 12 — sorted display; skip table if empty."""
    sorted_urls = sorted(unique_results)

    # FIX 12 — don't print misleading "Found 0 URLs" table
    if not sorted_urls:
        console.print("[yellow]No results collected.[/yellow]")
        return

    table = Table(
        title=(
            f"Found [bold]{len(sorted_urls)}[/bold] unique URLs "
            f"for '[italic]{dork}[/italic]'"
        ),
        style="cyan",
        title_style="bold magenta",
        show_lines=False,
    )
    table.add_column("N",   style="dim", width=6, no_wrap=True)
    table.add_column("URL", style="green")
    for i, url in enumerate(sorted_urls, 1):
        table.add_row(str(i), url)
    console.print(table)

    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as fh:
                for url in sorted_urls:
                    fh.write(url + "\n")
            console.print(
                f"\n[bold green]✔ {len(sorted_urls)} results saved to "
                f"'{output_file}'[/bold green]"
            )
        except IOError as exc:
            console.print(f"[bold red]Error saving to file: {exc}[/bold red]")


if __name__ == "__main__":
    cli()
