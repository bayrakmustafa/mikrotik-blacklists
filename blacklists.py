#!/usr/bin/env python3
import os
import re
import requests
from urllib.parse import urlparse

# === Settings ===
url_list_file = "blacklist_urls.txt"
output_dir = "mikrotik_blacklists"
temp_dir = "tmp_blacklists"

os.makedirs(output_dir, exist_ok=True)
os.makedirs(temp_dir, exist_ok=True)

print("[+] Reading URL list...")


# Normalize function: remove www prefix
def normalize_domain(m_domain):
    return m_domain[4:] if m_domain.startswith("www.") else m_domain


# Function to detect plain domain list files (no IP at start)
def is_plain_domain_list(m_lines):
    domain_only_count = 0
    for m_line in m_lines[:100]:
        m_line_strip = m_line.strip()
        if not m_line_strip or m_line_strip.startswith("#"):
            continue
        if not m_line_strip.startswith("0.0.0.0") and not m_line_strip.startswith("127.0.0.1"):
            domain_only_count += 1
    return domain_only_count >= 50  # If more than 50% of the first 100 lines are domain-only, treat as plain domain list


# === Read URLs and file names in order ===
urls_info = []
with open(url_list_file, "r") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue  # Skip empty and comment lines
        if ':' in line:
            url_part, file_part = line.rsplit(":", 1)
            url = url_part.strip()
            file_name = file_part.strip().lower().replace("_", "-")
            if not file_name.endswith(".txt"):
                file_name += ".txt"
            urls_info.append((url, file_name))
        else:
            url = line.strip()
            parsed = urlparse(url)
            if "githubusercontent.com" in parsed.netloc:
                parts = parsed.path.strip("/").split("/")
                user_part = parts[0] if len(parts) > 0 else "github"
            else:
                user_part = parsed.netloc.split('.')[-2]
            file_part = os.path.basename(parsed.path).replace("?", "_").replace("&", "_").replace("=", "_")
            base_name = f"{user_part}-{file_part}".lower().replace("_", "-")
            if not base_name.endswith(".txt"):
                base_name += ".txt"
            urls_info.append((url, base_name))

# === Store normalized domains per file ===
file_domains = {}
domain_to_file = {}

# === 1️⃣ Download files and extract domains ===
for url, base_name in urls_info:
    temp_path = os.path.join(temp_dir, base_name)

    if os.path.exists(temp_path):
        print(f"[+] File already exists, skipping download: {base_name}")
    else:
        print(f"[+] Downloading: {url} → {base_name}")
        r = requests.get(url, timeout=60)
        with open(temp_path, "w", encoding="utf-8", errors="ignore") as f:
            f.write(r.text)

    domains = set()
    with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.strip().startswith("#"):
                continue  # Ignore comment lines when extracting domains
            m = re.search(r"([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", line)
            if m:
                d = m.group(1)
                normalized = normalize_domain(d)
                domains.add(normalized)

    file_domains[base_name] = domains

    # Mark the first file for each normalized domain
    for d in domains:
        if d not in domain_to_file:
            domain_to_file[d] = base_name

print("[+] Domain ownership mapping completed.")

# === 2️⃣ Filter each file ===
for file_name, domains in file_domains.items():
    print(f"[+] Processing: {file_name}")

    temp_path = os.path.join(temp_dir, file_name)
    cleaned_path = os.path.join(output_dir, file_name)

    with open(temp_path, "r", encoding="utf-8", errors="ignore") as infile:
        lines = infile.readlines()

    header = []
    data_lines = []
    header_done = False

    # Separate header first (before first domain line)
    for line in lines:
        line_strip = line.strip()
        if not header_done:
            if not line_strip.startswith("#") and line_strip != "":
                m = re.search(r"([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", line_strip)
                if m:
                    header_done = True
                    data_lines.append(line)
                else:
                    header.append(line)
            else:
                header.append(line)
        else:
            data_lines.append(line)

    # Check and convert plain domain list format
    if is_plain_domain_list(data_lines):
        print(f"[+] Detected plain domain list format in {file_name}. Converting to hosts format.")
        new_lines = []
        for line in data_lines:
            line_strip = line.strip()
            if line_strip and not line_strip.startswith("#"):
                new_lines.append(f"0.0.0.0 {line_strip}\n")
            else:
                new_lines.append(line)
        data_lines = new_lines

    with open(cleaned_path, "w", encoding="utf-8") as outfile:
        outfile.writelines(header)

        block = []
        keep_block = False
        total_kept = 0
        total_skipped = 0

        for idx, line in enumerate(data_lines):
            line_strip = line.strip()

            if line_strip.startswith("#"):
                block.append(line)  # Always keep comment lines
            elif line_strip == "":
                block.append(line)  # Keep empty lines
            else:
                # Check only non-comment, non-empty lines
                domain_match = re.search(r"([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", line)
                if domain_match:
                    domain = domain_match.group(1)
                    normalized = normalize_domain(domain)

                    # Check numeric-only domain
                    first_part = normalized.split(".")[0]
                    if first_part.isdigit() and len(normalized.split(".")) == 1:
                        total_skipped += 1
                        continue  # Skip numeric-only domains

                    owner_file = domain_to_file.get(normalized)
                    if owner_file == file_name and not domain.startswith("www."):
                        block.append(line)
                        keep_block = True
                        total_kept += 1
                    else:
                        total_skipped += 1
                else:
                    total_skipped += 1

            # Check if block should be flushed
            next_is_comment = False
            if idx + 1 < len(data_lines):
                next_line_strip = data_lines[idx + 1].strip()
                if next_line_strip.startswith("#"):
                    next_is_comment = True

            is_last_line = (idx == len(data_lines) - 1)
            if next_is_comment or is_last_line:
                if keep_block:
                    outfile.writelines(block)
                block = []
                keep_block = False

        print(f"[SUMMARY] {file_name}: Kept {total_kept} lines, Skipped {total_skipped} lines")
    print(f"    ➜ Cleaned: {cleaned_path}")

print("[✓] Done! Results are in the 'mikrotik_blacklists' folder.")

# Cleanup temporary directory
for file_name in os.listdir(temp_dir):
    file_path = os.path.join(temp_dir, file_name)
    if os.path.isfile(file_path):
        os.remove(file_path)
print("[+] Temporary files cleaned up.")

# Remove the temporary directory
os.rmdir(temp_dir)
