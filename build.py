import sys
import json
import shutil
import hashlib
import logging
import platform as _platform
import subprocess
import logging.config
from pathlib import Path

import requests

ERRORS = {
    'UNSUPPORTED_PLATFORM': 1,
    'AMBIGUOUS_INSTALL': 2,
    'HASH_MISMATCH': 3,
}
TARGET_PYTHON_VERSION = '3.11.6'
HASH_BUFF_SIZE = 65536
HERE = Path(__file__).parent

BUILD_DIRECTORY = HERE.joinpath('build')
shutil.rmtree(BUILD_DIRECTORY, ignore_errors=True)
BUILD_DIRECTORY.mkdir()

ASSEMBLE_DIRECTORY = BUILD_DIRECTORY.joinpath('assemble')
shutil.rmtree(ASSEMBLE_DIRECTORY, ignore_errors=True)
ASSEMBLE_DIRECTORY.mkdir()

DIST_DIRECTORY = HERE.joinpath('dist')
shutil.rmtree(DIST_DIRECTORY, ignore_errors=True)
DIST_DIRECTORY.mkdir()


logging.config.dictConfig(
    {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "simple": {
                "format": "%(levelname)-8s: %(asctime)s: %(message)s"
            }
        },
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "level": "DEBUG",
                "formatter": "simple",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.FileHandler",
                "formatter": "simple",
                "level": "DEBUG",
                "filename": HERE.joinpath('build.log'),
                "mode": "w"
            }
        },
        "root": {
            "level": "DEBUG",
            "handlers": [
                "stdout",
                "file"
            ]
        }
    }
)
log = logging.getLogger(__name__)

def fail_for_unsupported_platform(platform):
    log.critical(f"Unable to continue due to unsupported platform: {platform}")
    sys.exit(ERRORS['UNSUPPORTED_PLATFORM'])

def fail_for_ambiguity(release_names):
    log.critical(f"Unable to determine correct Python build out of: {', '.join(release_names)}")
    sys.exit(ERRORS['AMBIGUOUS_INSTALL'])

def fail_for_hash_mismatch(expected, actual):
    log.critical(f"Unable to continue. Release file does not match hash. Expected: '{expected}', Actual: '{actual}'")
    sys.exit(ERRORS['HASH_MISMATCH'])

def get_file_sha256(p):
    sha256 = hashlib.sha256()
    with p.open('rb') as fp:
        while True:
            data = fp.read(HASH_BUFF_SIZE)
            if not data:
                break
            else:
                sha256.update(data)
    return sha256.hexdigest()

platform = sys.platform
if platform.startswith('linux'):
    platform = 'linux'
elif platform.startswith('win32'):
    platform = 'windows'
elif platform.startswith('darwin'):
    platform = 'darwin'
# The elifs here are strictly here for documentation if we need to
# support these platforms in the future
elif platform.startswith('freebsd'):
    fail_for_unsupported_platform(platform)
elif platform.startswith('aix'):
    fail_for_unsupported_platform(platform)
elif platform.startswith('emscripten'):
    fail_for_unsupported_platform(platform)
elif platform.startswith('wasi'):
    fail_for_unsupported_platform(platform)
elif platform.startswith('cygwin'):
    fail_for_unsupported_platform(platform)
else:
    fail_for_unsupported_platform(platform)

requirements_file = HERE.joinpath(f'{platform}-requirements.txt')

# Download a prebuilt Python distribution
# First, get the latest release tag
response = requests.get(
    'https://raw.githubusercontent.com/indygreg/python-build-standalone/latest-release/latest-release.json',
)
response_json = response.json()
tag = response_json['tag']

# Second, Get the release ID from Github API
response = requests.get(
    f'https://api.github.com/repos/indygreg/python-build-standalone/releases/tags/{tag}',
    headers={
        'X-GitHub-Api-Version': '2022-11-28',
        'Accept': 'application/vnd.github+json'
    }
)
response_json = response.json()
assets = response_json['assets']
release_names = [d['name'] for d in assets]

# Filter to install_only build configuration
release_names = [name for name in release_names if 'install_only' in name]

# Filter to only the python version that we are interested in
release_names = [name for name in release_names if TARGET_PYTHON_VERSION in name]

# Filter to only platform and architecture that we are interested in
if platform == 'windows':
    release_names = [name for name in release_names if 'windows-msvc-shared' in name]
elif platform == 'linux':
    release_names = [name for name in release_names if 'unknown-linux-gnu' in name]
elif platform == 'darwin':
    release_names = [name for name in release_names if 'apple-darwin' in name]

# Filter to only bitness that we are interested in
if platform == "windows":
    if sys.maxsize > 2**32:
        release_names = [name for name in release_names if 'x86_64' in name]
    else:
        release_names = [name for name in release_names if 'i686' in name]
elif platform == "linux":
    if sys.maxsize > 2**32:
        release_names = [name for name in release_names if 'x86_64' in name]
        release_names = [name for name in release_names if 'x86_64_v2' not in name]
        release_names = [name for name in release_names if 'x86_64_v3' not in name]
        release_names = [name for name in release_names if 'x86_64_v4' not in name]
    else:
        release_names = [name for name in release_names if 'i686' in name]
elif platform == "darwin":
    if _platform.machine() == "x86_64":
        release_names = [name for name in release_names if 'x86_64' in name]
    elif _platform.machine() == "arm64":
        release_names = [name for name in release_names if 'aarch64' in name]

# There should be two: one for the hash and one for the actual release
if len(release_names) > 2:
    fail_for_ambiguity(release_names)

# Third, find the release file and the hash file
for name in release_names:
    if name.endswith('sha256'):
        hash_file = name
    else:
        release_file = name

log.debug(f'Found hash_file: {hash_file}')
log.debug(f'Found release_file: {release_file}')

# Pull out the url for the assets we identified
for asset in assets:
    if asset['name'] == hash_file:
        # get url for hash_file
        hash_file_url = asset['url']
    elif asset['name'] == release_file:
        # get url for release_file
        release_file_url = asset['url']

log.debug(f'Found hash_file_url: {hash_file_url}')
log.debug(f'Found release_file_url: {release_file_url}')

# Fourth Download Hash file
hash_file_response = requests.get(
    hash_file_url,
    headers={
        'X-GitHub-Api-Version': '2022-11-28',
        'Accept': 'application/octet-stream'
    }
)
hash_file_path = BUILD_DIRECTORY.joinpath(hash_file)
with hash_file_path.open('wb') as fp:
    fp.write(hash_file_response.content)
hash_hex = hash_file_path.read_text().strip()
log.debug(hash_hex)

# Fifth Download Release file
release_file_response = requests.get(
    release_file_url,
    headers={
        'X-GitHub-Api-Version': '2022-11-28',
        'Accept': 'application/octet-stream'
    }
)
release_file_path = BUILD_DIRECTORY.joinpath(release_file)
with release_file_path.open('wb') as fp:
    fp.write(release_file_response.content)

# Verify the hash of the downloaded release file
release_file_sha256 = get_file_sha256(release_file_path)
if hash_hex != release_file_sha256:
    fail_for_hash_mismatch(hash_hex, release_file_sha256)
else:
    log.info("Downloaded Python release matches expected hash value")

# Extract the release file into the dist directory
shutil.unpack_archive(
    release_file_path,
    ASSEMBLE_DIRECTORY,
)

# Move everything under python directory into subdirectory based on python version
src_dir = ASSEMBLE_DIRECTORY.joinpath('python')
dst_dir = src_dir.joinpath(TARGET_PYTHON_VERSION)
for item in src_dir.iterdir():
    if item != dst_dir:
        shutil.move(item, dst_dir.joinpath(item.name))

if platform == "windows":
    python_executable = ASSEMBLE_DIRECTORY.joinpath('python').joinpath(TARGET_PYTHON_VERSION).joinpath('python')
else:
    python_executable = ASSEMBLE_DIRECTORY.joinpath('python').joinpath(TARGET_PYTHON_VERSION).joinpath('bin').joinpath('python3')

# Upgrade pip
command = f'{python_executable} -m pip install --upgrade pip'
subprocess.run(command, shell=True)

# Install dependencies
command = f'{python_executable} -m pip install -r {requirements_file}'
subprocess.run(command, shell=True)

# Install mast package
command = f'{python_executable} -m pip install {HERE}'
subprocess.run(command, shell=True)

# Copy all files from files/mast_home
files_directory = HERE.joinpath('files')
mast_home_directory = files_directory.joinpath('mast_home')
for item in mast_home_directory.iterdir():
    if item.is_dir():
        shutil.copytree(
            item,
            ASSEMBLE_DIRECTORY.joinpath(item.name)
        )
    else:
        log.critical("Non-directory item in files/mast_home")
        sys.exit(5)

# Copy all invocation scripts from files/invocation_scripts/{platform} to DIST_DIRECTORY
script_dir = files_directory.joinpath('invocation_scripts').joinpath(platform)
for item in script_dir.iterdir():
    if item.name.startswith('set-env'):
        contents = item.read_text()
        contents = contents.replace('{PYTHON_VERSION}', TARGET_PYTHON_VERSION)
        ASSEMBLE_DIRECTORY.joinpath(item.name).write_text(contents)
    else:
        shutil.copy(item, ASSEMBLE_DIRECTORY.joinpath(item.name))

shutil.make_archive(
    DIST_DIRECTORY.joinpath(
        f'MAST-{TARGET_PYTHON_VERSION}_{platform}',
    ),
    format='zip',
    root_dir=ASSEMBLE_DIRECTORY,
    base_dir='.',
)
