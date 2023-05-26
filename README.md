# NAME

faescape - abscond from FurAffinity and mirror stuff to other sites

# SYNOPSIS

Currently only the downloading portion exists in a command line version. Usage is as follows on Linux and macOS. On Windows, using [virtual environments](https://docs.python.org/3/library/venv.html) and setting environment variables works differently and I don't know how.

Setting it up:

```sh
# Set up a virtual environment.
python -m venv env
# Activate it.
. env/bin/activate
# Install dependencies.
pip -r requirements.txt
```

To run it, you need to grab the `a` and `b` cookies from your browser that's logged into FurAffinity. Usually you can get at them by hitting F12 to show developer tools, visit an FA page and look in the Network tab. Those need to be set as environment variables.

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

# DESCRIPTION

It's just an archive script right now.

Contributions welcome. Use [black](https://pypi.org/project/black/) to format the code so that it's all equal(ly ugly.)

# LICENSE

MIT, see [LICENSE.txt](LICENSE.txt).
