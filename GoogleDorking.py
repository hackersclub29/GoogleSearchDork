#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import sys
from pathlib import Path
from typing import List, Optional, Set

import aiohttp
import click
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.table import Table

# Initialize Rich Console for beautiful output
console = Console()

class GoogleSearchClient:
    """
    An asynchronous client for performing Google Custom Search API queries.
    Manages API key rotation, concurrent requests, and result processing.
    """

    BASE_URL = "https://www.googleapis.com/customsearch/v1"
    CX = "b2208abe579f5466e"  # Your Custom Search Engine ID

    def __init__(self, api_keys: List[str]):
        if not api_keys:
            raise ValueError("API key list cannot be empty.")
        self.api_keys = api_keys
        self.current_key_index = 0
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    def _get_next_key(self) -> str:
        """Rotates to the next available API key."""
        key = self.api_keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
        return key

    async def search(self, query: str, start_index: int = 1) -> Optional[List[str]]:
        """
        Performs a single asynchronous search request and handles API key rotation on quota errors.
        """
        if not self._session:
            raise RuntimeError("ClientSession not initialized. Use 'async with'.")

        max_attempts = len(self.api_keys)
        for attempt in range(max_attempts):
            api_key = self._get_next_key()
            params = {
                "key": api_key,
                "cx": self.CX,
                "q": query,
                "start": start_index,
                "num": 10
            }
            try:
                async with self._session.get(self.BASE_URL, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        return [item.get("link") for item in data.get("items", []) if item.get("link")]
                    elif response.status == 429: # Quota exceeded
                        console.log(f"[yellow]API key ending in '...{api_key[-4:]}' reached its quota. Rotating...[/yellow]")
                        await asyncio.sleep(1) # Wait a moment before retrying with the next key
                        continue
                    else:
                        error_text = await response.text()
                        console.log(f"[red]API Error: {response.status} - {error_text}[/red]")
                        return None
            except aiohttp.ClientError as e:
                console.log(f"[red]Network Error: {e}[/red]")
                return None
        console.log("[bold red]All API keys have reached their quota.[/bold red]")
        return None

@click.group()
def cli():
    """A high-performance Google Dorking tool."""
    pass

@cli.command()
@click.option("--dork", required=True, help="The search dork query.")
@click.option("--sets", default=1, type=int, help="Number of 100-result sets to query.")
@click.option("--output-file", type=click.Path(), help="File to save the results to.")
@click.option("--verbose", is_flag=True, help="Enable verbose logging.")
def search(dork: str, sets: int, output_file: Optional[str], verbose: bool):
    """
    Performs multiple asynchronous Google searches to gather unique URLs.
    """
    if verbose:
        import logging
        logging.basicConfig(level="INFO", handlers=[RichHandler(console=console, rich_tracebacks=True)])

    try:
        api_keys = Path("api.txt").read_text().splitlines()
        api_keys = [key.strip() for key in api_keys if key.strip()]
        if not api_keys:
            console.print("[bold red]Error: 'api.txt' is empty or does not exist.[/bold red]")
            sys.exit(1)
    except FileNotFoundError:
        console.print("[bold red]Error: 'api.txt' not found. Please create it with one API key per line.[/bold red]")
        sys.exit(1)

    asyncio.run(run_searches(dork, sets, output_file, api_keys))

async def run_searches(dork: str, sets: int, output_file: Optional[str], api_keys: List[str]):
    """Main async orchestrator for running search tasks."""
    unique_results: Set[str] = set()
    tasks = []

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console,
    )

    with progress:
        description = f"Querying {sets * 10} pages..."
        task_id = progress.add_task(description, total=sets * 10)

        async with GoogleSearchClient(api_keys) as client:
            for set_index in range(sets):
                # Google's API performs better with slight query variations for deep pagination
                modified_dork = f'{dork} "{chr(65 + set_index)}"'
                for page in range(1, 11): # 10 pages per set to get up to 100 results
                    start_index = (page - 1) * 10 + 1
                    task = client.search(modified_dork, start_index)
                    tasks.append(task)
                    progress.update(task_id, advance=1)

            results_pages = await asyncio.gather(*tasks)

    for page_results in results_pages:
        if page_results:
            unique_results.update(page_results)

    # Display results in a table
    table = Table(title=f"Found {len(unique_results)} Unique URLs for '{dork}'", style="cyan", title_style="bold magenta")
    table.add_column("N", style="dim", width=6)
    table.add_column("URL", style="green")

    for i, link in enumerate(unique_results, 1):
        table.add_row(str(i), link)
    console.print(table)

    # Save to file if requested
    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                for link in sorted(unique_results):
                    f.write(link + "\n")
            console.print(f"\n[bold green]✔ Results saved to '{output_file}'[/bold green]")
        except IOError as e:
            console.print(f"[bold red]Error saving to file: {e}[/bold red]")


if __name__ == "__main__":
    cli()
