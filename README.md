# GoogleSearchDork

A Python-based utility compiled with PyInstaller for Linux that automates Google Dorking using the Google Custom Search API. It supports multiple API keys for seamless rotation upon quota exhaustion, ensuring uninterrupted data retrieval.

GitHub Repository: [GoogleSearchDork](https://github.com/hackersclub29/GoogleSearchDork.git)

## Features

- **Automated Dorking**: Performs multiple queries by appending variations to the base dork, retrieving extensive search results.
- **API Key Rotation**: Automatically switches between multiple API keys stored in `api.txt` when quota limits are reached.
- **Duplicate Handling**: Ensures unique URLs are logged, avoiding redundancy in results.
- **Custom Output**: Allows users to specify output filenames for logging results.
- **User-Friendly Interface**: Interactive prompts guide users through the search and logging process.
- **Compiled Binary**: Delivered as a compiled binary for Linux for convenient execution without requiring Python setup.

## Installation

1. **Clone the Repository**:

   ```bash
   git clone https://github.com/hackersclub29/GoogleSearchDork.git
   cd GoogleSearchDork
   ```

2. **Run the Compiled Binary (Linux)**:

   Make sure the binary is executable:

   ```bash
   chmod +x GoogleSearchDork
   ./GoogleSearchDork
   ```

3. **For Source Users** (if you use `GoogleSearchDork.py`):

   Ensure Python 3.x is installed.

   The tool depends on the `requests` library. If the tool does not work, install it manually using:

   ```bash
   pip3 install requests
   ```

4. **Prepare API Keys**:

   Create an `api.txt` file in the project directory and list your Google Custom Search API keys, one per line.

   ```txt
   YOUR_API_KEY_1
   YOUR_API_KEY_2
   ...
   ```

## Usage

Run the binary (Linux only):

```bash
./GoogleSearchDork
```

Or run via Python if using the script:

```bash
python3 GoogleSearchDork.py
```

Follow the on-screen prompts to:

- Enter your dork query.
- Specify the number of query sets (each set retrieves up to 100 results).
- Choose whether to save the output and specify the filename.

*Example*:

```
[+] Enter The Dork Search Query: inurl:admin login
[+] Enter number of query sets (each set returns up to 100 results): 2
[+] Do You Want to Save the Output? (Y/N): y
[+] Enter Output Filename: admin_logins.txt
```

## Configuration

- **Custom Search Engine ID (CX)**: The script uses a predefined CX value. If you have a different CX, modify the `CX` variable in the script accordingly.

To get a **Google Custom Search API Key** and create a programmable search engine, follow these steps:

1. Go to the [Google Cloud Console](https://console.cloud.google.com/apis/library/customsearch.googleapis.com)
2. Enable the **Custom Search API** for your project.
3. Visit [Programmable Search Engine](https://programmablesearchengine.google.com/about/) to create your custom search engine.
4. Copy your **API Key** from the Cloud Console.
5. Get your **Search Engine ID (CX)** from the Programmable Search Engine control panel.

## Notes

- Ensure that your API keys have sufficient quota. The script will automatically switch to the next key in `api.txt` if the current key exceeds its quota.
- The Google Custom Search API limits the number of results per query. This tool mitigates that by performing multiple modified queries.
- If the tool doesn't work and `requests` is not installed, run `pip3 install requests` to fix it.

## License

This project is licensed under the [MIT License](LICENSE).

## Contributing

Contributions are welcome! Please fork the repository and submit a pull request for any enhancements or bug fixes.

## Disclaimer

This tool is intended for educational and ethical testing purposes only. The author is not responsible for any misuse or damage caused by this tool. Use responsibly and ensure compliance with all applicable laws and regulations.


