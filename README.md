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

## GUI Mode

Run the program without any arguments and it'll start up in GUI mode. Here you can kick off the download of your archive and split the archive into chunks afterwards, if you want to import into PostyBirb (see below.)

The GUI is pretty ugly, sorry. But you (hopefully) only have to use it once ever, so it should be okay.

## Command Line Mode

To run it in command-line mode, you need to grab the `a` and `b` cookies from your browser that's logged into FurAffinity. Usually you can get at them by hitting F12 to show developer tools, visit an FA page and look in the Network tab. Those need to be set as environment variables.

```sh
# Set cookies.
export FA_ARCHIVE_A_COOKIE='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
export FA_ARCHIVE_B_COOKIE='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
# Activate the virtual environment (once per shell session.)
. env/bin/activate
# Smoke it.
./fa_archive.py ARTIST_NAME DIRECTORY_NAME
# Split it up afterwards for importing into PostyBirb.
./fa_archive.py chunk DIRECTORY_NAME CHUNK_SIZE
```

This will download all the stuff from ARTIST\_NAME's gallery, scraps and journals into the directory DIRECTORY\_NAME (which will be created if it doesn't exist yet.) The downloads are throttled to a very slow speed so you don't get banned, so this is supposed to keep running in the background for a while. You can terminate it at any time and it will pick back up where it left off.

Afterwards, the archive needs to be split up into chunks if you want to import them into PostyBirb (see below.)

## PostyBirb Integration

You can import your archives into [PostyBirb](https://www.postybirb.com/), which in turn can post them to a whole bunch of other sites.

You have to split up the archives into smaller chunks, since PostyBirb gets really slow if you have too many submissions in it at once. Keep it below 100, the GUI uses 50 as a reasonable default value.

Import *and* post each of these chunks separately. Once you posted one chunk, move on to the next. Don't pile them up together or else PostyBirb *will* become really slow or even lock up entirely.

## Development

Contributions welcome. Use [black](https://pypi.org/project/black/) to format the code so that it's all equal(ly ugly.)

# LICENSE

MIT, see [LICENSE.txt](LICENSE.txt).
