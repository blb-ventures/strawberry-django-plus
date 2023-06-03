
The automatic optimization is enabled by adding the `DjangoOptimizerExtension` to your
strawberry's schema config.


```python
import strawberry
from strawberry_django_plus.optimizer import DjangoOptimizerExtension

schema = strawberry.Schema(
    Query,
    extensions=[
        # other extensions...
        DjangoOptimizerExtension,
    ]
)
```

Now consider the following:
!!! Example
    === "models"
        ```python

        class Artist(models.Model):
            name = models.CharField()


        class Album(models.Moodel):
            name = models.CharField()
            release_date = models.DateTimeField()
            artist = models.ForeignKey("Artist", related_name="albums")


        class Song(models.Model):
            name = model.CharField()
            duration = models.DecimalField()
            album = models.ForeignKey("Album", related_name="songs")
        ```
    === "schema"
        ```python
        from strawberry_django_plus import gql

        @gql.django.type(Artist)
        class ArtistType:
            name: auto
            albums: "List[AlbumType]"


        @gql.django.type(Album)
        class AlbumType:
            name: auto
            release_date: auto
            artist: ArtistType
            songs: "List[SongType]"


        @gql.django.type(Song)
        class SongType:
            name: auto
            duration: auto
            album_type: AlbumType


        @gql.type
        class Query:
            artist: Artist = gql.django.field()
            songs: List[SongType] = gql.django.field()
        ```

    === "query for the artist field"
        ```gql
        query {
          artist {
            id
            name
            albums {
              id
              name
              songs {
                id
                name
              }
            }
          }
        }
        ```
    === "optimized queryset for the artist field"
        ```python
        Artist.objects.all().only("id", "name").prefetch_related(
            Prefetch(
                "albums",
                queryset=Album.objects.all().only("id", "name").prefetch_related(
                    "songs",
                    Song.objects.all().only("id", "name"),
                )
            ),
        )
        ```
    === "query for the song field"

        ```gql
        query {
          song {
            id
            album
            id
            name
            artist {
              id
              name
              albums {
                id
                name
                release_date
              }
            }
          }
        }

        ```
    === "optimized queryset for the song field"
        ```python
        Song.objects.all().only(
            "id",
            "album",
            "album__id",
            "album__name",
            "album__release_date",  # Note about this below
            "album__artist",
            "album__artist__id",
        ).select_related(
            "album",
            "album__artist",
        ).prefetch_related(
            "album__artist__albums",
            Prefetch(
                "albums",
                Album.objects.all().only("id", "name", "release_date"),
            )
        )
        ```

!!! Note
    Even though `album__release_date` field was not selected here, it got selected
    in the prefetch query later. Since Django caches known objects, we have to select it here or
    else it would trigger extra queries latter.

### Model property

It is possible to include hints for non-model fields using the field api or even our
`@model_property` (or its cached variation, `@cached_model_property`) decorator on the model
itself, for people who like to keep all the business logic at the model.

For example, the following will automatically optimize [`only`](https://docs.djangoproject.com/en/4.0/ref/models/querysets/#only) and [`select_related`](https://docs.djangoproject.com/en/4.0/ref/models/querysets/#django.db.models.query.QuerySet.select_related) if that
field gets selected:

```python
from strawberry_django_plus import gql

class Song(models.Model):
    name = models.CharField()

    @gql.model_property(only=["name", "album__name"], select_related=["album"])
    def name_with_album(self) -> str:
        return f"{self.album.name}: {self.name}"

@gql.django.type(Song)
class SongType:
    name: auto
    name_with_album: str
```

Another option would be to define that on the field itself:

```python
@gql.django.type(Song)
class SongType:
    name: auto
    name_with_album: str = gql.django.field(
        only=["name", "album__name"],
        select_related=["album"],
    )
```
