# NAME

faescape - abscond from FurAffinity and mirror stuff to other sites

# SYNOPSIS

For pre-built Windows versions, look at the [Releases section](https://github.com/askmeaboutlo0m/faescape/releases).

On other platform, read below on how to set it up.

# DESCRIPTION

Install Python. On Linux and macOS, you probably already have it. Windows has to get it from the Microsoft Store, Chocolatey or whatever else y'all do there.

Set up the project on Linux and macOS:

```sh
python -m venv env
. env/bin/activate
pip -r requirements.txt
```

On Windows it's this instead:

```bat
python -m venv env
env\Scripts\activate.bat
pip -r requirements.txt
```

Run the program without any arguments and it'll start up in GUI mode.

To run it in command-line mode, you need to grab the `a` and `b` cookies from your browser that's logged into FurAffinity. Usually you can get at them by hitting F12 to show developer tools, visit an FA page and look in the Network tab. Those need to be set as environment variables.

```sh
# Set cookies.
export FA_ARCHIVE_A_COOKIE='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
export FA_ARCHIVE_B_COOKIE='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
# Activate the virtual environment (once per shell session.)
. env/bin/activate
# Smoke it.
./fa_archive.py ARTIST_NAME DIRECTORY_NAME
```

This will download all the stuff from ARTIST\_NAME's gallery, scraps and journals into the directory DIRECTORY\_NAME (which will be created if it doesn't exist yet.) The downloads are throttled to a very slow speed so you don't get banned, so this is supposed to keep running in the background for a while. You can terminate it at any time and it will pick back up where it left off.

Contributions welcome. Use [black](https://pypi.org/project/black/) to format the code so that it's all equal(ly ugly.)

# LICENSE

MIT, see [LICENSE.txt](LICENSE.txt).
