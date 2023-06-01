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
import sqlite3
import sys

logging.basicConfig(
    format="%(levelname)s: %(message)s",
    level=getattr(logging, os.environ.get("FA_ARCHIVE_LOG_LEVEL", "DEBUG")),
)


# We're not in a hurry, delay every request by at least 10 seconds so that we
# don't get banned for blasting FA too fast.
class DelayedFAAPI(faapi.FAAPI):
    @faapi.FAAPI.crawl_delay.getter
    def crawl_delay(self):
        delay = faapi.FAAPI.crawl_delay.fget(self)
        return delay if delay > 10 else 10


class FaArchiver:
    EXTENSION_RE = re.compile(r"(\.[^\./]+)$")

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

    def archive(self):
        logging.info("Archiving artist '%s'", self._artist)
        self._connect_api()
        self._create_directories()
        self._init_db()
        self._check_artist()
        self._collect_archive_elements()
        self._download_archive_elements()

    def _connect_api(self):
        logging.debug("Connecting API")
        self._api = DelayedFAAPI(cookies)
        self._check_logged_in()

    def _check_logged_in(self):
        user = self._api.me()
        if user:
            logging.info("Logged in as '%s%s'", user.status, user.name)
        else:
            raise RuntimeError("Looks like you're not logged in")

    def _create_directories(self):
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
        logging.debug("Initializing database '%s'", self._db_file)
        self._db = sqlite3.connect(self._db_file)
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
                    archived integer not null,
                    unique (type, element_id))
                """
            )

    def _check_artist(self):
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
        self._collect_archive_element_type("gallery", self._get_gallery_page)
        self._collect_archive_element_type("scraps", self._get_scraps_page)
        self._collect_archive_element_type("journals", self._get_journals_page)

    def _get_gallery_page(self, page):
        logging.debug("Get gallery page %d", page)
        return self._api.gallery(self._artist, page)

    def _get_scraps_page(self, page):
        logging.debug("Get scraps page %d", page)
        return self._api.scraps(self._artist, page)

    def _get_journals_page(self, page):
        logging.debug("Get journals page %d", page)
        return self._api.journals(self._artist, page)

    def _collect_archive_element_type(self, element_type, get_page_fn):
        state_key = "collected_{}".format(element_type)
        if self._get_state_bool(state_key):
            logging.debug("Already collected %s", element_type)
        else:
            logging.info("Collecting %s", element_type)
            results = self._get_all_pages(get_page_fn)
            with self._db as con:
                for result in results:
                    self._insert_archive_element(con, element_type, result.id)
                self._set_state(con, state_key, 1)

    def _get_all_pages(self, get_page_fn):
        page = 1
        all_results = []
        while True:
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
            db_id, element_type, element_id = element
            if element_type == "gallery":
                logging.info("Downloading gallery submission %d", element_id)
                self._download_submission(element_id, self._gallery_dir)
            elif element_type == "scraps":
                logging.info("Downloading scraps submission %d", element_id)
                self._download_submission(element_id, self._scraps_dir)
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

    # Database access.

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
        with contextlib.closing(self._db.cursor()) as cur:
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

    def _insert_archive_element(self, con, element_type, element_id):
        con.execute(
            """
            insert into archive_element(type, element_id, archived)
            values (?, ?, 0)
            """,
            (element_type, element_id),
        )

    def _get_next_open_archive_element(self):
        with contextlib.closing(self._db.cursor()) as cur:
            cur.execute(
                """
                select id, type, element_id from archive_element
                where not archived order by id limit 1
                """
            )
            return cur.fetchone()

    def _close_archive_element(self, db_id):
        with self._db as con:
            con.execute(
                "update archive_element set archived = 1 where id = ?", (db_id,)
            )


if __name__ == "__main__":
    error = False

    argc = len(sys.argv)
    if argc == 3:
        artist = sys.argv[1]
        base_dir = sys.argv[2]
    else:
        error = True
        logging.error(
            "Usage: %s ARTIST_NAME_FROM_URL DIRECTORY",
            "fa_archive" if argc < 1 else sys.argv[0],
        )

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
