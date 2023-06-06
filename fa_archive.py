#!/usr/bin/env python
# Copyright (c) 2023 askmeaboutloom
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
import contextlib
import datetime
import faapi
import json
import logging
import os
import re
import requests
import shutil
import sqlite3
import sys

logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=getattr(logging, os.environ.get("FA_ARCHIVE_LOG_LEVEL", "DEBUG")),
)


# We're not in a hurry, delay every request by at least 5 seconds so that we
# don't get banned for blasting FA too fast.
class DelayedFAAPI(faapi.FAAPI):
    @faapi.FAAPI.crawl_delay.getter
    def crawl_delay(self):
        delay = faapi.FAAPI.crawl_delay.fget(self)
        return delay if delay > 5 else 5


class StopArchiving(Exception):
    pass


class FaArchiver:
    EXTENSION_RE = re.compile(r"(\.[^\./]+)$")
    SUBMISSION_RE = re.compile(r"^([0-9]+)([dft])\.")

    def __init__(self, artist, base_dir, cookies):
        self._artist = artist
        self._base_dir = base_dir
        self._cookies = cookies
        self._gallery_dir = os.path.join(base_dir, "gallery")
        self._scraps_dir = os.path.join(base_dir, "scraps")
        self._journals_dir = os.path.join(base_dir, "journals")
        self._db_file = os.path.join(base_dir, "archive.db")
        self._api = None
        self._db = None
        self._cancelled = False

    def archive(self):
        self._check_cancelled()
        logging.info("Archiving artist '%s'", self._artist)
        self._connect_api()
        self._create_directories()
        self._init_db()
        self._check_artist()
        self._collect_archive_elements()
        self._download_archive_elements()
        logging.info("Done archiving artist '%s'", self._artist)
        logging.info(
            "If you want to import this data into PostyBirb, "
            + "you have to split it into chunks now."
        )

    def cancel(self):
        self._cancelled = True

    def _check_cancelled(self):
        if self._cancelled:
            raise StopArchiving()

    def _connect_api(self):
        self._check_cancelled()
        logging.debug("Connecting API")
        self._api = DelayedFAAPI(self._cookies)
        self._check_logged_in()

    def _check_logged_in(self):
        user = self._api.me()
        if user:
            logging.info("Logged in as '%s%s'", user.status, user.name)
        else:
            raise RuntimeError("Looks like you're not logged in")

    def _create_directories(self):
        self._check_cancelled()
        logging.debug("Creating directories")
        self._create_directory(self._base_dir)
        self._create_directory(self._gallery_dir)
        self._create_directory(self._scraps_dir)
        self._create_directory(self._journals_dir)

    def _create_directory(self, directory):
        try:
            os.mkdir(directory)
            logging.debug("Created directory '%s'", directory)
        except FileExistsError:
            logging.debug("Directory '%s' already exists", directory)

    def _init_db(self):
        self._check_cancelled()
        logging.debug("Initializing database '%s'", self._db_file)
        self._open_db()
        with self._db as con:
            con.execute(
                """
                create table if not exists state (
                    key text primary key not null,
                    value)
                """
            )
            con.execute(
                """
                create table if not exists archive_element (
                    id integer primary key not null,
                    type text not null,
                    element_id integer not null,
                    element_data,
                    archived integer not null,
                    unique (type, element_id))
                """
            )

    def _check_artist(self):
        self._check_cancelled()
        db_artist = self._get_state_string("artist")
        if db_artist is None:
            self._check_artist_exists()
        elif db_artist.casefold() == self._artist.casefold():
            logging.debug("Artist '%s' exists according to database", db_artist)
        else:
            raise RuntimeError(
                "Directory already contains data for artist '{}'".format(db_artist)
            )

    def _check_artist_exists(self):
        logging.debug("Checking if artist '%s' is valid", self._artist)
        try:
            user = self._api.user(self._artist)
        except Exception as err:
            raise RuntimeError("Artist '{}' not found".format(self._artist)) from err
        logging.info("Target artist: %s%s", user.status, user.name)
        with self._db as con:
            self._set_state(con, "artist", self._artist)

    # "Collection" of archive elements: going through the pages and grabbing
    # all ids of stuff that needs to be downloaded.

    def _collect_archive_elements(self):
        self._check_cancelled()
        self._collect_archive_element_type(
            "gallery", self._get_gallery_page, self._insert_submission_element
        )
        self._check_cancelled()
        self._collect_archive_element_type(
            "scraps", self._get_scraps_page, self._insert_submission_element
        )
        self._check_cancelled()
        self._collect_archive_element_type(
            "journals", self._get_journals_page, self._insert_journal_element
        )

    def _get_gallery_page(self, page):
        logging.debug("Get gallery page %d", page)
        return self._api.gallery(self._artist, page)

    def _get_scraps_page(self, page):
        logging.debug("Get scraps page %d", page)
        return self._api.scraps(self._artist, page)

    def _get_journals_page(self, page):
        logging.debug("Get journals page %d", page)
        return self._api.journals(self._artist, page)

    def _insert_submission_element(self, con, element_type, result):
        self._insert_archive_element(con, element_type, result.id, None)
        if result.thumbnail_url:
            self._insert_archive_element(
                con, element_type + "_thumb", result.id, result.thumbnail_url
            )

    def _insert_journal_element(self, con, element_type, result):
        self._insert_archive_element(con, element_type, result.id, None)

    def _collect_archive_element_type(self, element_type, get_page_fn, insert_fn):
        state_key = "collected_{}".format(element_type)
        if self._get_state_bool(state_key):
            logging.debug("Already collected %s", element_type)
        else:
            logging.info("Collecting %s", element_type)
            results = self._get_all_pages(get_page_fn)
            with self._db as con:
                for result in results:
                    insert_fn(con, element_type, result)
                self._set_state(con, state_key, 1)

    def _get_all_pages(self, get_page_fn):
        page = 1
        all_results = []
        while True:
            self._check_cancelled()
            page_results, next_page = get_page_fn(page)
            logging.debug("%d results on page %d", len(page_results), page)
            all_results += page_results
            if next_page is None:
                logging.debug("%d results total", len(all_results))
                return all_results
            elif next_page > page:
                page = next_page
            else:
                raise ValueError(
                    "Next page {} <= current page {}".format(next_page, page)
                )

    # The actual downloading of stuff to archive.

    def _download_archive_elements(self):
        while element := self._get_next_open_archive_element():
            self._check_cancelled()
            db_id, element_type, element_id, element_data = element
            if element_type == "gallery":
                logging.info("Downloading gallery submission %d", element_id)
                self._download_submission(element_id, self._gallery_dir)
            elif element_type == "gallery_thumb":
                logging.info("Downloading gallery thumbnail %d", element_id)
                self._download_thumbnail(element_id, element_data, self._gallery_dir)
            elif element_type == "scraps":
                logging.info("Downloading scraps submission %d", element_id)
                self._download_submission(element_id, self._scraps_dir)
            elif element_type == "scraps_thumb":
                logging.info("Downloading scraps thumbnail %d", element_id)
                self._download_thumbnail(element_id, element_data, self._scraps_dir)
            elif element_type == "journals":
                logging.info("Downloading journal %d", element_id)
                self._download_journal(element_id, self._journals_dir)
            else:
                raise ValueError("Unknown element type '%s'".format(element_type))
            self._close_archive_element(db_id)

    def _download_submission(self, submission_id, directory):
        info, data = self._api.submission(submission_id, get_file=True)
        ext = self._extract_file_extension(info.file_url)
        self._spew_json(info, os.path.join(directory, "{}d.json".format(submission_id)))
        self._spew_bytes(
            data, os.path.join(directory, "{}f{}".format(submission_id, ext))
        )

    def _download_thumbnail(self, submission_id, thumbnail_url, directory):
        self._api.handle_delay()
        data = self._api.session.get(thumbnail_url, timeout=self._api.timeout).content
        ext = self._extract_file_extension(thumbnail_url)
        self._spew_bytes(
            data, os.path.join(directory, "{}t{}".format(submission_id, ext))
        )

    def _download_journal(self, journal_id, directory):
        info = self._api.journal(journal_id)
        self._spew_json(info, os.path.join(directory, "{}d.json".format(journal_id)))

    @staticmethod
    def _extract_file_extension(url):
        match = FaArchiver.EXTENSION_RE.search(url)
        if match:
            return match[1]
        else:
            logging.warning("Unknown file extension in %s", url)
            return ""

    @staticmethod
    def _spew_json(info, path):
        logging.debug("Writing %s", path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(info, f, default=FaArchiver._to_json, sort_keys=True, indent=2)

    @staticmethod
    def _to_json(obj):
        if isinstance(obj, datetime.datetime):
            return obj.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            return dict(obj)

    @staticmethod
    def _spew_bytes(data, path):
        logging.debug("Writing %s", path)
        with open(path, "wb") as f:
            f.write(data)

    # Chunking

    def chunk(self, chunk_size):
        if chunk_size < 1 or chunk_size > 99999:
            raise ValueError(
                "Invalid chunk size {}, must be between 1 and 99999".format(chunk_size)
            )

        if not os.path.isfile(self._db_file):
            raise RuntimeError(
                (
                    "Database {} doesn't exist, either you picked the wrong "
                    + "directory or you didn't archive anything yet"
                ).format(self._db_file)
            )

        self._open_db()

        open_count = self._count_open_elements()
        if open_count != 0:
            raise RuntimeError(
                (
                    "Can't chunk an incomplete archive, there's still "
                    + "{} element(s) left to download"
                ).format(open_count)
            )

        self._chunk_submissions(self._gather_to_chunk(), chunk_size)
        logging.info(
            "Done splitting archive into chunks, you can "
            + "start importing them into PostyBirb now."
        )

    def _gather_to_chunk(self):
        submissions = []
        submissions += self._gather_to_chunk_from(self._gallery_dir, "gallery")
        submissions += self._gather_to_chunk_from(self._scraps_dir, "scraps")
        return sorted(submissions, key=lambda submission: submission["id"])

    def _gather_to_chunk_from(self, dir, location):
        submissions_by_id = {}
        for name in os.listdir(dir):
            path = os.path.join(dir, name)
            match = FaArchiver.SUBMISSION_RE.search(name)
            if match:
                submission_id = match[1]
                file_type = match[2]

                if submission_id in submissions_by_id:
                    submission = submissions_by_id[submission_id]
                else:
                    submission = {"id": int(submission_id), "location": location}
                    submissions_by_id[submission_id] = submission

                if file_type == "d":
                    submission["data"] = path
                elif file_type == "f":
                    submission["file"] = path
                elif file_type == "t":
                    submission["thumb"] = path
                else:
                    raise NotImplemented(file_type)
            else:
                logging.warning("Not an archive file: '{}'".format(path))
        return list(submissions_by_id.values())

    def _chunk_submissions(self, submissions, chunk_size):
        chunk_base_dir = os.path.join(self._base_dir, "chunk{}".format(chunk_size))
        os.mkdir(chunk_base_dir)
        offset = 0
        index = 0
        while offset < len(submissions):
            self._make_chunk(
                index, submissions[offset : offset + chunk_size], chunk_base_dir
            )
            offset += chunk_size
            index += 1

    def _make_chunk(self, index, submissions, chunk_base_dir):
        chunk_dir = os.path.join(chunk_base_dir, "{:05d}".format(index + 1))
        logging.info("Creating chunk %s", chunk_dir)
        os.mkdir(chunk_dir)
        self._write_archive_chunk(chunk_dir, index)
        self._write_chunk_files(chunk_dir, submissions)

    def _write_archive_chunk(self, chunk_dir, index):
        path = os.path.join(chunk_dir, "archive.chunk")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{}\n".format(index))

    def _write_chunk_files(self, chunk_dir, submissions):
        gallery_dir = os.path.join(chunk_dir, "gallery")
        scraps_dir = os.path.join(chunk_dir, "scraps")
        os.mkdir(gallery_dir)
        os.mkdir(scraps_dir)
        for submission in submissions:
            target_dir = (
                gallery_dir if submission["location"] == "gallery" else scraps_dir
            )
            for key in ["data", "file", "thumb"]:
                if key in submission:
                    shutil.copy2(submission[key], target_dir)

    # Database access.

    def _open_db(self):
        self._db = sqlite3.connect(self._db_file)

    def _get_state_bool(self, key):
        value = self._get_state_int(key)
        return value is not None and value != 0

    def _get_state_int(self, key):
        with contextlib.closing(self._db.cursor()) as cur:
            cur.execute(
                "select cast(value as integer) from state where key = ?", (key,)
            )
            row = cur.fetchone()
            return row[0] if row else None

    def _get_state_string(self, key):
        return FaArchiver.get_state_string(self._db, key)

    @staticmethod
    def get_state_string(db, key):
        with contextlib.closing(db.cursor()) as cur:
            cur.execute("select cast(value as text) from state where key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def _set_state(self, con, key, value):
        con.execute(
            """
            insert into state(key, value) values (?, ?)
            on conflict do update set value = excluded.value
            """,
            (key, value),
        )

    def _insert_archive_element(self, con, element_type, element_id, element_data):
        con.execute(
            """
            insert into archive_element(type, element_id, element_data, archived)
            values (?, ?, ?, 0)
            """,
            (element_type, element_id, element_data),
        )

    def _get_next_open_archive_element(self):
        with contextlib.closing(self._db.cursor()) as cur:
            cur.execute(
                """
                select id, type, element_id, element_data from archive_element
                where not archived order by id limit 1
                """
            )
            return cur.fetchone()

    def _close_archive_element(self, db_id):
        with self._db as con:
            con.execute(
                "update archive_element set archived = 1 where id = ?", (db_id,)
            )

    def _count_open_elements(self):
        with contextlib.closing(self._db.cursor()) as cur:
            cur.execute("select count(*) from archive_element where not archived")
            return cur.fetchone()[0]


def main_cmd_archive(artist, base_dir):
    error = False
    cookies = requests.cookies.RequestsCookieJar()
    for letter in ["a", "b"]:
        env_key = "FA_ARCHIVE_{}_COOKIE".format(letter.upper())
        try:
            cookies.set(letter, os.environ[env_key])
        except KeyError:
            error = True
            logging.error(
                "Missing required environment variable '%s'. Set it to the "
                + "value of your '%s' cookie from your logged-in browser.",
                env_key,
                letter,
            )

    if error:
        sys.exit(2)

    FaArchiver(artist, base_dir, cookies).archive()


def main_cmd_chunk(base_dir, chunk_size):
    FaArchiver(None, base_dir, None).chunk(chunk_size)


def main_gui():
    from logging.handlers import QueueHandler
    from queue import Empty, SimpleQueue
    from threading import Thread
    from tkinter import StringVar, Tk, filedialog, messagebox
    from tkinter.scrolledtext import ScrolledText
    from tkinter.ttk import Button, Entry, Frame, Label

    PADX = 4
    PADY = 4

    root = Tk()
    root.winfo_toplevel().title("FurAffinity Archiver")
    root.geometry("800x600")

    frm = Frame(root)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)
    frm.rowconfigure(6, weight=1)

    Label(frm, text="Output directory:").grid(
        column=0, row=0, padx=PADX, pady=PADY, sticky="ew"
    )
    Label(frm, text="Artist:").grid(column=0, row=1, padx=PADX, pady=PADY, sticky="ew")
    Label(frm, text="Cookie a:").grid(
        column=0, row=2, padx=PADX, pady=PADY, sticky="ew"
    )
    Label(frm, text="Cookie b:").grid(
        column=0, row=3, padx=PADX, pady=PADY, sticky="ew"
    )
    Label(frm, text="Chunk size:").grid(
        column=0, row=4, padx=PADX, pady=PADY, sticky="ew"
    )

    base_dir_var = StringVar()
    base_dir_entry = Entry(frm)
    base_dir_entry["textvariable"] = base_dir_var
    base_dir_entry.grid(column=1, row=0, padx=PADX, pady=PADY, sticky="ew")

    def guess_artist(path):
        db_file = os.path.join(path, "archive.db")
        if os.path.exists(db_file):
            db = sqlite3.connect(db_file)
            with db as con:
                artist = FaArchiver.get_state_string(con, "artist")
                if artist:
                    artist_var.set(artist)

    def choose_base_dir():
        path = filedialog.askdirectory(
            parent=root, initialdir=os.path.dirname(__file__)
        )
        if path:
            base_dir_var.set(path)
            try:
                guess_artist(path)
            except Exception as e:
                logging.warning("Error guessing artist: %s", e)

    choose_button = Button(frm, text="Choose...", command=choose_base_dir)
    choose_button.grid(column=2, row=0, padx=PADX, pady=PADY)

    artist_var = StringVar()
    artist_entry = Entry(frm)
    artist_entry["textvariable"] = artist_var
    artist_entry.grid(column=1, row=1, columnspan=2, padx=PADX, pady=PADY, sticky="ew")

    a_cookie_var = StringVar()
    a_cookie_var.set(os.environ.get("FA_ARCHIVE_A_COOKIE", ""))
    a_cookie_entry = Entry(frm)
    a_cookie_entry["textvariable"] = a_cookie_var
    a_cookie_entry.grid(
        column=1, row=2, columnspan=2, padx=PADX, pady=PADY, sticky="ew"
    )

    b_cookie_var = StringVar()
    b_cookie_var.set(os.environ.get("FA_ARCHIVE_B_COOKIE", ""))
    b_cookie_entry = Entry(frm)
    b_cookie_entry["textvariable"] = b_cookie_var
    b_cookie_entry.grid(
        column=1, row=3, columnspan=2, padx=PADX, pady=PADY, sticky="ew"
    )

    chunk_size_var = StringVar()
    chunk_size_var.set("50")
    chunk_size_entry = Entry(frm)
    chunk_size_entry["textvariable"] = chunk_size_var
    chunk_size_entry.grid(
        column=1, row=4, columnspan=2, padx=PADX, pady=PADY, sticky="ew"
    )

    button_frm = Frame(frm)
    button_frm.grid(column=0, row=5, columnspan=3, sticky="ew")

    text = ScrolledText(frm, state="disabled", wrap="word")
    text.grid(column=0, row=6, columnspan=4, padx=PADX, pady=PADY, sticky="nsew")

    queue = SimpleQueue()
    formatter = logging.Formatter("%(levelname)s: %(message)s\n")
    logging.getLogger().addHandler(QueueHandler(queue))
    logging.info(
        "Fill in the fields above and press the Download Archive button to start."
    )
    logging.info("Output directory is the folder to download stuff to.")
    logging.info("Artist is the FurAffinity username you want to archive.")
    logging.info(
        "Cookie a and cookie b are your FurAffinity login cookies. "
        + "You can probably get these out of your browser by opening the "
        + "developer console (hit F12), opening the Network tab and visiting "
        + "any FurAffinity page while logged in."
    )
    logging.warning(
        "DO NOT SHARE YOUR LOGIN COOKIES WITH ANYONE ELSE. "
        + "They are similar to a password. Keep them to yourself."
    )
    logging.info(
        "After downloading, you can split up the archive into chunks for importing "
        + "into PostyBirb. It's recommended that you keep the chunk size at less than "
        + "100, since PostyBirb gets very slow with too many submissions at once."
    )

    quit_requested = False
    archiver_finished = False
    archiver_instance = None

    def update_log():
        have_message = False
        while True:
            try:
                record = queue.get_nowait()
                message = formatter.format(record)
                text["state"] = "normal"
                try:
                    text.insert("end", message)
                finally:
                    text["state"] = "disabled"
                have_message = True
            except Empty:
                break
        if have_message:
            text.see("end")

        nonlocal archiver_finished
        if archiver_finished:
            archiver_finished = False
            nonlocal archiver_instance
            archiver_instance = None
            archive_button["text"] = "Archive"
            archive_button["state"] = "normal"
            chunk_button["state"] = "normal"
            if quit_requested:
                root.destroy()
                sys.exit(0)

        root.after(100, update_log)

    update_log()

    def make_archiver():
        errors = []
        base_dir = base_dir_var.get().strip()
        if not base_dir:
            errors.append("Missing output directory. Choose where to archive to.")
        artist = artist_var.get().strip()
        if not artist:
            errors.append("Missing artist. Enter a username.")
        a_cookie = a_cookie_var.get().strip()
        if not a_cookie:
            errors.append("Missing cookie a. Get it out of your logged-in browser.")
        b_cookie = b_cookie_var.get().strip()
        if not b_cookie:
            errors.append("Missing cookie b. Get it out of your logged-in browser.")
        if errors:
            messagebox.showerror(
                title="Error", message="\n\n".join(errors), parent=root
            )
            return None
        else:
            cookies = requests.cookies.RequestsCookieJar()
            cookies.set("a", a_cookie)
            cookies.set("b", b_cookie)
            return FaArchiver(artist, base_dir, cookies)

    def run_archive_thread(archiver):
        try:
            logging.info("*** Archiving Started ***")
            archiver.archive()
        except StopArchiving:
            logging.info("Cancelled, will pick up at this point again next time")
        except Exception as e:
            logging.error(str(e))
            raise
        finally:
            nonlocal archiver_finished
            archiver_finished = True
            logging.info("*** Archiving Ended ***")

    def start_cancel():
        nonlocal archiver_instance
        if archiver_instance:
            logging.info(
                "Cancelling at the next opportunity, may take 10 seconds or so..."
            )
            archiver_instance.cancel()
        else:
            archiver_instance = make_archiver()
            if archiver_instance:
                archive_button["text"] = "Cancel"
                chunk_button["state"] = "disabled"
                Thread(target=run_archive_thread, args=(archiver_instance,)).start()

    archive_button = Button(button_frm, text="Download Archive", command=start_cancel)
    archive_button.grid(column=0, row=0, padx=PADX, pady=PADY)

    def request_quit():
        if archiver_instance:
            logging.info(
                "Quitting at the next opportunity, may take 10 seconds or so..."
            )
            archiver_instance.cancel()
            nonlocal quit_requested
            quit_requested = True
        else:
            root.destroy()
            sys.exit(0)

    def run_chunk_thread(archiver, chunk_size):
        try:
            logging.info("*** Chunking Started ***")
            archiver.chunk(chunk_size)
        except Exception as e:
            logging.error(str(e))
            raise
        finally:
            nonlocal archiver_finished
            archiver_finished = True
            logging.info("*** Chunking Ended ***")

    def chunk_up():
        nonlocal archiver_instance
        if archiver_instance:
            return

        errors = []
        base_dir = base_dir_var.get().strip()
        if not base_dir:
            errors.append("Missing output directory to chunk.")

        chunk_size_string = chunk_size_var.get()
        try:
            chunk_size = int(chunk_size_string)
            if chunk_size < 1 or chunk_size > 99999:
                raise ValueError()
        except Exception as e:
            errors.append(
                "Invalid chunk size. Must be a whole number between 1 and 99999"
            )

        if errors:
            messagebox.showerror(
                title="Error", message="\n\n".join(errors), parent=root
            )
        else:
            archiver_instance = FaArchiver(None, base_dir, None)
            archive_button["state"] = "disabled"
            chunk_button["state"] = "disabled"
            Thread(
                target=run_chunk_thread, args=(archiver_instance, chunk_size)
            ).start()

    chunk_button = Button(
        button_frm, text="Split Archive for PostyBirb", command=chunk_up
    )
    chunk_button.grid(column=1, row=0, padx=PADX, pady=PADY)

    quit_button = Button(button_frm, text="Quit", command=request_quit)
    quit_button.grid(column=2, row=0, padx=PADX, pady=PADY)
    root.protocol("WM_DELETE_WINDOW", request_quit)

    root.mainloop()


if __name__ == "__main__":
    argc = len(sys.argv)
    if argc == 1:
        main_gui()
    elif argc == 3:
        main_cmd_archive(sys.argv[1], sys.argv[2])
    elif argc == 4 and sys.argv[1] == "chunk":
        main_cmd_chunk(sys.argv[2], int(sys.argv[3]))
    else:
        program = "fa_archive" if argc < 1 else sys.argv[0]
        logging.error("GUI usage: %s (without arguments)", program)
        logging.error(
            "Command line archiving: %s ARTIST_NAME_FROM_URL DIRECTORY", program
        )
        logging.error("Command line chunking: %s chunk DIRECTORY CHUNK_SIZE", program)
