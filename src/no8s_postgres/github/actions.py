"""GitHub Actions helpers — artifact download from workflow runs."""

import io
import json
import zipfile

import aiohttp


async def download_artifact_content(download_url: str, token: str) -> dict:
    """
    Download a GitHub Actions zip artifact and return its JSON content.

    Fetches the artifact zip at download_url (using the provided GitHub token),
    extracts the first .json file found, and returns the parsed dict.

    Args:
        download_url: The archive_download_url from the GitHub API.
        token: GitHub personal access token.

    Returns:
        Parsed JSON dict from the artifact.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.read()

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith(".json"):
                return json.loads(zf.read(name))

    raise ValueError("No JSON file found in artifact zip")
