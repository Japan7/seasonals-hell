#!/usr/bin/env python3
from __future__ import annotations

import datetime
import enum
import functools
from operator import attrgetter
from typing import Optional

import toolz
import pydantic
import requests
import typer


class PageInfo(pydantic.BaseModel):
    hasNextPage: bool


@functools.total_ordering
class Date(pydantic.BaseModel):
    year: Optional[int]
    month: Optional[int]
    day: Optional[int]

    def __eq__(self, other: Date):
        return (self.year, self.month, self.day) == (other.year, other.month, other.day)

    def __lt__(self, other: Date):
        if self.year is None:
            return True
        if other.year is None:
            return False
        if self.year < other.year:
            return True

        if self.month is None:
            return True
        if other.month is None:
            return False
        if self.month < other.month:
            return True

        if self.day is None:
            return True
        if other.day is None:
            return False
        if self.day < other.day:
            return True

        return False


class Tag(pydantic.BaseModel):
    id: int
    name: str
    category: str
    rank: int


class Title(pydantic.BaseModel):
    romaji: str
    english: Optional[str]
    native: str
    userPreferred: str


class MediaEdge(pydantic.BaseModel):
    relationType: str


class MediaConnection(pydantic.BaseModel):
    edges: list[MediaEdge]


class Media(pydantic.BaseModel):
    id: int
    startDate: Date
    isAdult: bool
    episodes: Optional[int]
    isLicensed: bool
    genres: list[str]
    tags: list[Tag]
    title: Title
    relations: MediaConnection
    format: Optional[str]


class AnimePage(pydantic.BaseModel):
    pageInfo: PageInfo
    media: list[Media]


class MediaSeason(enum.Enum):
    WINTER = "hivers"
    SPRING = "printemps"
    SUMMER = "été"
    FALL = "automne"


season_query = """
query ($page: Int, $season: MediaSeason, $year: Int) {
  Page(page: $page) {
    pageInfo {
      hasNextPage
    }
    media(season: $season, seasonYear: $year) {
      id
      startDate {
        year
        month
        day
      }
      episodes
      isAdult
      isLicensed
      relations {
        edges {
          relationType
        }
      }
      genres
      tags {
        id
        name
        category
        rank
      }
      title {
        romaji
        english
        native
        userPreferred
      }
      format
    }
  }
}
"""


AL_URL = 'https://graphql.anilist.co'


def get_anime(season: MediaSeason, year: int, page: int = 1) -> list[Media]:
    variables = {
        "page": page,
        "season": season.name,
        "year": year
    }

    data = {
        "query": season_query,
        "variables": variables
    }

    resp = requests.post(AL_URL, json=data)
    page_resp = AnimePage.model_validate(resp.json()["data"]["Page"])

    medias: list[Media] = []
    for media in page_resp.media:
        if media.format in ("MOVIE", "OVA", "SPECIAL", "MUSIC"):
            continue
        if media.isAdult:
            continue
        if any(rel.relationType in ("PREQUEL", "SEQUEL")
               for rel in media.relations.edges):
            continue

        medias.append(media)

    if page_resp.pageInfo.hasNextPage:
        medias.extend(get_anime(season, year, page=page + 1))

    return medias


class MediaEntry(pydantic.BaseModel):
    id: int
    mediaId: int
    status: str
    progress: int
    score: int


class MediaList(pydantic.BaseModel):
    entries: list[MediaEntry]


class MediaListCollection(pydantic.BaseModel):
    lists: list[MediaList]


app = typer.Typer()


user_query = """
query ($username: String, $type: MediaType) {
  MediaListCollection(userName: $username, type: $type, status_not: PLANNING) {
    lists {
      entries {
        id
        mediaId
        status
        progress
        score
      }
    }
  }
}
"""


def get_userlist(username: str) -> list[MediaEntry]:
    variables = {
        'username': username,
        'type': "ANIME"
    }

    data = {
        'query': user_query,
        'variables': variables
    }

    req = requests.post(AL_URL, json=data)
    if req.status_code != 200:
        print(req.json())
        req.raise_for_status()
    user_list = MediaListCollection.model_validate(req.json()['data']['MediaListCollection'])

    return list(toolz.concat(l.entries for l in user_list.lists))


def get_season(year: Optional[int] = None,
               season: Optional[MediaSeason] = None) -> tuple[int, MediaSeason]:

    date = datetime.datetime.now()
    if year is None:
        year = date.year if date.month < 12 else date.year + 1

    if season is None:
        if date.month < 2 or date.month == 12:
            season = MediaSeason.WINTER
        elif date.month < 5:
            season = MediaSeason.SPRING
        elif date.month < 8:
            season = MediaSeason.SUMMER
        else:
            season = MediaSeason.FALL

    return year, season


@app.command()
def md_summary(year: Optional[int] = None, season: Optional[MediaSeason] = None):
    year, season = get_season(year, season)

    medias = get_anime(season=season, year=year)
    medias_format: dict[str, list[Media]] = toolz.groupby(attrgetter('format'), medias)
    print(f"# Saisonniers {season.value} {year}")

    for format, format_medias in medias_format.items():
        print(f"\n### {format or 'Unknown'}\n".replace('_', ' '))
        for media in sorted(format_medias, key=attrgetter('startDate')):
            print(f"- [ ] [{{AL}}](https://anilist.co/anime/{media.id}) "
                  f"{media.title.userPreferred}")


@app.command()
def user_progress(username: str,
                  year: Optional[int] = None,
                  season: Optional[MediaSeason] = None):
    year, season = get_season(year, season)

    medias: list[Media] = get_anime(season=season, year=year)
    user_list: list[MediaEntry] = get_userlist(username)

    user_media_ids: set[int] = set(e.mediaId for e in user_list)

    watched: list[Media] = []
    remaining: list[Media] = []

    for media in medias:
        if media.id in user_media_ids:
            watched.append(media)
        else:
            remaining.append(media)

    print("Watched:")
    for w in watched:
        print(w.title.userPreferred)

    print("\nRemaining:")
    for r in remaining:
        print(r.title.userPreferred, f"https://anilist.co/anime/{r.id}")


if __name__ == "__main__":
    app()
