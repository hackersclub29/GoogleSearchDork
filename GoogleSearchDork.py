#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import print_function
import sys
import time
import requests

# Check for Python 3.x
if sys.version_info[0] < 3:
    print("\n\033[91m[ERROR] This script requires Python 3.x\033[0m\n")
    sys.exit(1)

# ANSI color codes for styling output
class Colors:
    RED = "\033[91m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

# Read API keys from file "api.txt" (one key per line)
try:
    with open("api.txt", "r") as f:
        api_keys = [line.strip() for line in f if line.strip()]
except Exception as e:
    print(f"{Colors.RED}[ERROR] Could not read API keys file: {str(e)}{Colors.RESET}")
    sys.exit(1)

if not api_keys:
    print(f"{Colors.RED}[ERROR] No API keys found in api.txt.{Colors.RESET}")
    sys.exit(1)

# Global variable for tracking the current API key index
current_key_index = 0

# Custom Search Engine ID remains fixed.
CX = "b2208abe579f5466e"

# Default output filename
log_file = "dorks_output.txt"

def logger(data):
    """Logs data to a file."""
    with open(log_file, "a", encoding="utf-8") as file:
        file.write(data + "\n")

def google_custom_search(query, start_index=1, num_results=10):
    """
    Performs a search using the Google Custom Search API.
    
    Args:
        query (str): The search query.
        start_index (int): The starting index for the results (min 1, max 91).
        num_results (int): Number of results to fetch (max 10 per request).

    Returns:
        list: A list of result items (each is a dict) or an empty list if an error occurs.
    """
    global api_keys, current_key_index, CX
    url = "https://www.googleapis.com/customsearch/v1"
    max_attempts = len(api_keys)
    attempts = 0

    while attempts < max_attempts:
        key = api_keys[current_key_index]
        params = {
            "key": key,
            "cx": CX,
            "q": query,
            "start": start_index,
            "num": num_results
        }
        response = requests.get(url, params=params)
        if response.status_code == 200:
            results = response.json()
            return results.get("items", [])
        else:
            # Check if error indicates quota/limit exceeded and try next key if so
            if "limit" in response.text.lower() or "quota" in response.text.lower():
                print(f"{Colors.RED}[ERROR] API key {key} limit reached, trying next key.{Colors.RESET}")
                current_key_index = (current_key_index + 1) % len(api_keys)
                attempts += 1
                time.sleep(1)
                continue
            else:
                print(f"{Colors.RED}[ERROR] {response.status_code}: {response.text}{Colors.RESET}")
                return []
    print(f"{Colors.RED}[ERROR] All API keys have reached their limit.{Colors.RESET}")
    return []

def multi_query_dorks():
    """
    Performs multiple queries by slightly modifying the search query to retrieve more than 100 results.
    This version ensures no duplicate URLs are printed or logged by using a set.
    """
    global log_file  # Allow modification of the global log_file variable
    try:
        dork = input(f"\n{Colors.BLUE}[+] Enter The Dork Search Query: {Colors.RESET}").strip()
        if not dork:
            print(f"{Colors.RED}[ERROR] Query cannot be empty!{Colors.RESET}")
            sys.exit(1)

        sets_input = input(f"{Colors.BLUE}[+] Enter number of query sets (each set returns up to 100 results): {Colors.RESET}").strip()
        try:
            total_sets = int(sets_input)
            if total_sets <= 0:
                raise ValueError
        except ValueError:
            print(f"{Colors.RED}[ERROR] Invalid number entered!{Colors.RESET}")
            return

        save_output = input(f"\n{Colors.BLUE}[+] Do You Want to Save the Output? (Y/N): {Colors.RESET}").strip().lower()
        if save_output == "y":
            log_file = input(f"{Colors.BLUE}[+] Enter Output Filename: {Colors.RESET}").strip()
            if not log_file:
                log_file = "dorks_output.txt"
            if not log_file.endswith(".txt"):
                log_file += ".txt"

        print(f"\n{Colors.GREEN}[INFO] Starting multi-query search... Please wait...{Colors.RESET}\n")
        unique_results = set()

        # Iterate over query sets. Each set appends a trivial letter (A, B, C, …) to the base query.
        for set_index in range(total_sets):
            mod_query = f"{dork} {chr(65 + set_index)}"  # e.g., if set_index==0, query becomes "dork A"
            print(f"\n{Colors.GREEN}[INFO] Query Set {set_index+1}: {mod_query}{Colors.RESET}\n")
            page = 1  # Page counter for this set (each page returns up to 10 results)
            # Continue fetching until we attempt to retrieve 100 results in this set
            while len(unique_results) < (set_index + 1) * 100:
                start_index = (page - 1) * 10 + 1
                # The API limits the 'start' parameter to 91 (i.e., max of 100 results per query variant)
                if start_index > 91:
                    break
                batch_size = min(10, ((set_index + 1) * 100) - len(unique_results))
                results = google_custom_search(mod_query, start_index, batch_size)
                if not results:
                    break  # No more results for this set
                for result in results:
                    link = result.get("link")
                    if link not in unique_results:
                        print(f"{Colors.YELLOW}[+] {Colors.RESET}{link}")
                        unique_results.add(link)
                        if save_output == "y":
                            logger(link)
                page += 1
                time.sleep(1)  # Delay between API calls
            print(f"{Colors.GREEN}[✔] Query Set {set_index+1} completed: {len(unique_results)} unique results so far.{Colors.RESET}")
        print(f"\n{Colors.GREEN}[✔] Automation Done.. Total unique results fetched: {len(unique_results)}{Colors.RESET}")
    except KeyboardInterrupt:
        print(f"\n{Colors.RED}[!] User Interruption Detected! Exiting...{Colors.RESET}\n")
        sys.exit(1)
    except Exception as e:
        print(f"{Colors.RED}[ERROR] {str(e)}{Colors.RESET}")
    sys.exit()

if __name__ == "__main__":
    multi_query_dorks()
