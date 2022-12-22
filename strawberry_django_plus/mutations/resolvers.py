import dataclasses
from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Tuple,
    Type,
    TypedDict,
    TypeVar,
    Union,
    cast,
    overload,
)

from django.db import models, transaction
from django.db.models.base import Model
from django.db.models.fields.related import ManyToManyField
from django.db.models.fields.reverse_related import (
    ForeignObjectRel,
    ManyToManyRel,
    ManyToOneRel,
    OneToOneRel,
)
import strawberry
from strawberry import UNSET
from strawberry.file_uploads.scalars import Upload
from strawberry.types.info import Info
from strawberry_django.fields.types import (
    ManyToManyInput,
    ManyToOneInput,
    OneToManyInput,
    OneToOneInput,
)
from typing_extensions import TypeAlias

from strawberry_django_plus import relay
from strawberry_django_plus.types import ListInput, NodeInput
from strawberry_django_plus.utils import aio
from strawberry_django_plus.utils.inspect import get_model_fields
from strawberry_django_plus.utils.resolvers import resolve_sync

if TYPE_CHECKING:
    from django.db.models.manager import ManyToManyRelatedManager, RelatedManager

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_InputListTypes: TypeAlias = Union[strawberry.ID, "ParsedObject"]


class FullCleanOptions(TypedDict, total=False):
    exclude: List[str]
    validate_unique: bool
    validate_constraints: bool


def _parse_pk(
    value: Optional[Union["ParsedObject", strawberry.ID, _M]],
    model: Type[_M],
) -> Tuple[Optional[_M], Optional[Dict[str, Any]]]:
    if value is None:
        return None, None
    elif isinstance(value, Model):
        return value, None
    elif isinstance(value, ParsedObject):
        return value.parse(model)
    elif isinstance(value, dict):
        return None, value

    return model._default_manager.get(pk=value), None


def _parse_data(info: Info, model: Type[_M], value: Any):
    obj, data = _parse_pk(value, model)

    parsed_data = {}
    if data:
        for k, v in data.items():
            if v is UNSET:
                continue

            if isinstance(v, ParsedObject):
                if v.pk is None:
                    v = cast(_M, create(info, model(), v.data or {}))
                elif isinstance(v.pk, models.Model) and v.data:
                    v = update(info, v.pk, v.data)
                else:
                    v = v.pk

            if k == "through_defaults" or not obj or getattr(obj, k) != v:
                parsed_data[k] = v

    return obj, parsed_data


@dataclasses.dataclass
class ParsedObject:
    pk: Optional[Union[strawberry.ID, Model]]
    data: Optional[Dict[str, Any]] = None

    def parse(self, model: Type[_M]) -> Tuple[Optional[_M], Optional[Dict[str, Any]]]:
        if self.pk is None or self.pk is UNSET:
            return None, self.data
        elif isinstance(self.pk, models.Model):
            assert isinstance(self.pk, model)
            return self.pk, self.data
        else:
            return model._default_manager.get(pk=self.pk), self.data


@dataclasses.dataclass
class ParsedObjectList:
    add: Optional[List[_InputListTypes]] = None
    remove: Optional[List[_InputListTypes]] = None
    set: Optional[List[_InputListTypes]] = None  # noqa:A003


@overload
def parse_input(info: Info, data: Dict[str, _T]) -> Dict[str, _T]:
    ...


@overload
def parse_input(info: Info, data: List[_T]) -> List[_T]:
    ...


@overload
def parse_input(info: Info, data: relay.GlobalID) -> relay.Node:
    ...


@overload
def parse_input(info: Info, data: Any) -> Any:
    ...


def parse_input(info: Info, data: Any):
    if isinstance(data, dict):
        return {k: parse_input(info, v) for k, v in data.items()}
    elif isinstance(data, list):
        return [parse_input(info, v) for v in data]
    elif isinstance(data, relay.GlobalID):
        node = data.resolve_node(info, required=True)
        if aio.is_awaitable(node, info=info):
            node = resolve_sync(node)
        return node
    elif isinstance(data, NodeInput):
        pk = cast(Any, parse_input(info, getattr(data, "id", UNSET)))
        parsed = {}
        for field in dataclasses.fields(data):
            if field.name == "id":
                continue
            parsed[field.name] = parse_input(info, getattr(data, field.name))
        return ParsedObject(
            pk=pk,
            data=parsed if len(parsed) else None,
        )
    elif isinstance(data, (OneToOneInput, OneToManyInput)):
        return ParsedObject(
            pk=parse_input(info, data.set),
        )
    elif isinstance(data, (ManyToOneInput, ManyToManyInput, ListInput)):
        d = getattr(data, "data", None)
        if dataclasses.is_dataclass(d):
            d = {f.name: parse_input(info, getattr(data, f.name)) for f in dataclasses.fields(d)}
        return ParsedObjectList(
            add=cast(List[_InputListTypes], parse_input(info, data.add)),
            remove=cast(List[_InputListTypes], parse_input(info, data.remove)),
            set=cast(List[_InputListTypes], parse_input(info, data.set)),
        )
    elif dataclasses.is_dataclass(data):
        return {f.name: parse_input(info, getattr(data, f.name)) for f in dataclasses.fields(data)}

    return data


@overload
def create(
    info: Info,
    model: Type[_M],
    data: Dict[str, Any],
    *,
    full_clean: Union[bool, FullCleanOptions] = True,
) -> _M:
    ...


@overload
def create(
    info: Info,
    model: Type[_M],
    data: List[Dict[str, Any]],
    *,
    full_clean: Union[bool, FullCleanOptions] = True,
) -> List[_M]:
    ...


@transaction.atomic
def create(
    info,
    model,
    data,
    *,
    full_clean: Union[bool, FullCleanOptions] = True,
):
    if isinstance(data, list):
        return [create(info, model, d, full_clean=full_clean) for d in data]
    elif dataclasses.is_dataclass(data):
        data = vars(cast(object, data))
    return update(info, model(), data, full_clean=full_clean)


@overload
def update(
    info: Info,
    instance: _M,
    data: Dict[str, Any],
    *,
    full_clean: Union[bool, FullCleanOptions] = True,
) -> _M:
    ...


@overload
def update(
    info: Info,
    instance: Iterable[_M],
    data: Dict[str, Any],
    *,
    full_clean: Union[bool, FullCleanOptions] = True,
) -> List[_M]:
    ...


@transaction.atomic
def update(info, instance, data, *, full_clean: Union[bool, FullCleanOptions] = True):
    if isinstance(instance, Iterable):
        many = True
        instances = list(instance)
        if not instances:
            return []
    else:
        many = False
        instances = [instance]

    obj_models = [obj.__class__ for obj in instances]
    assert len(set(obj_models)) == 1
    fields = get_model_fields(obj_models[0])
    files: List[
        Tuple[
            models.FileField,
            Union[Upload, Literal[False]],
        ]
    ] = []
    m2m: List[Tuple[Union[ManyToManyField, ForeignObjectRel], Any]] = []

    if dataclasses.is_dataclass(data):
        data = vars(data)

    for name, value in data.items():
        field = fields.get(name)

        if field is None or value is UNSET:
            continue
        elif isinstance(field, models.FileField):
            if value is None:
                # We want to reset the file field value when None was passed in the input, but
                # `FileField.save_form_data` ignores None values. In that case we manually pass
                # False which clears the file.
                value = False
            # set filefields at the same time so their hooks can use other set values
            files.append((field, value))
            continue
        elif isinstance(field, (ManyToManyField, ForeignObjectRel)):
            # m2m will be processed later
            m2m.append((field, value))
            continue
        elif isinstance(field, models.ForeignKey) and isinstance(
            value,
            # We are using str here because strawberry.ID can't be used for isinstance
            (ParsedObject, str),
        ):
            value, value_data = _parse_data(info, field.related_model, value)
            # If value is None, that means we should create the model
            if value is None:
                value = field.related_model._default_manager.create(**value_data)
            else:
                update(info, value, value_data, full_clean=full_clean)

        for instance in instances:
            update_field(info, instance, field, value)

    for instance in instances:
        for file_field, value in files:
            file_field.save_form_data(instance, value)

        full_clean_options = full_clean if isinstance(full_clean, dict) else {}
        if full_clean:
            instance.full_clean(**full_clean_options)

        instance.save()

        for field, value in m2m:
            update_m2m(info, instance, field, value)

    return instances if many else instances[0]


@overload
def delete(
    info: Info,
    instance: _M,
    *,
    data: Optional[Dict[str, Any]] = None,
) -> _M:
    ...


@overload
def delete(
    info: Info,
    instance: Iterable[_M],
    *,
    data: Optional[Dict[str, Any]] = None,
) -> List[_M]:
    ...


@transaction.atomic
def delete(info, instance, *, data=None):
    if isinstance(instance, Iterable):
        many = True
        instances = list(instance)
    else:
        many = False
        instances = [instance]

    assert len({obj.__class__ for obj in instances}) == 1
    for instance in instances:
        pk = instance.pk
        instance.delete()
        # After the instance is deleted, set its ID to the original database's
        # ID so that the success response contains ID of the deleted object.
        instance.pk = pk

    return instances if many else instances[0]


def update_field(info: Info, instance: Model, field: models.Field, value: Any):
    if value is UNSET:
        return

    data = None
    if value and isinstance(field, models.ForeignObject) and not isinstance(value, Model):
        value, data = _parse_pk(value, field.related_model)

    field.save_form_data(instance, value)
    # If data was passed to the foreign key, update it recursively
    if data and value:
        update(info, value, data)


def update_m2m(
    info: Info,
    instance: Model,
    field: Union[ManyToManyField, ForeignObjectRel],
    value: Any,
):
    if value is UNSET:
        return

    if isinstance(field, OneToOneRel):
        remote_field = field.remote_field
        value, data = _parse_pk(value, remote_field.model)
        if value is None:
            value = getattr(instance, field.name)
        else:
            remote_field.save_form_data(value, instance)
            value.save()

        # If data was passed to the field, update it recursively
        if data:
            update(info, value, data)
        return

    use_remove = True
    if isinstance(field, ManyToManyField):
        manager = cast("RelatedManager", getattr(instance, field.attname))
    else:
        assert isinstance(field, (ManyToManyRel, ManyToOneRel))
        accessor_name = field.get_accessor_name()
        assert accessor_name
        manager = cast("RelatedManager", getattr(instance, accessor_name))
        if field.one_to_many:
            # remove if field is nullable, otherwise delete
            use_remove = field.remote_field.null is True

    to_add = []
    to_remove = []
    to_delete = []
    need_remove_cache = False

    values = value.set if isinstance(value, ParsedObjectList) else value
    if isinstance(values, list):
        if isinstance(value, ParsedObjectList) and getattr(value, "add", None):
            raise ValueError("'add' cannot be used together with 'set'")
        if isinstance(value, ParsedObjectList) and getattr(value, "remove", None):
            raise ValueError("'remove' cannot be used together with 'set'")

        existing = set(manager.all())
        need_remove_cache = need_remove_cache or bool(values)
        for v in values:
            obj, data = _parse_data(info, manager.model, v)

            if obj:
                if data:
                    if obj in existing and hasattr(manager, "through"):
                        through_defaults = data.pop("through_defaults", {})
                        if through_defaults:
                            manager = cast("ManyToManyRelatedManager", manager)
                            intermediate_model = manager.through
                            im = intermediate_model._base_manager.get(  # type:ignore
                                **{
                                    manager.source_field_name: instance,  # type:ignore
                                    manager.target_field_name: obj,  # type:ignore
                                }
                            )

                            for k, v in through_defaults.items():
                                setattr(im, k, v)
                            im.save()

                        if data:
                            for k, v in data.items():
                                setattr(obj, k, v)
                            obj.save()
                    elif obj in existing:
                        for k, v in data.items():
                            setattr(obj, k, v)
                        obj.save()
                    else:
                        manager.add(obj, **data)
                else:
                    if obj not in existing:
                        to_add.append(obj)

                existing.discard(obj)
            else:
                manager.create(**data)

        for remaining in existing:
            if use_remove:
                to_remove.append(remaining)
            else:
                to_delete.append(remaining)

    else:
        need_remove_cache = need_remove_cache or bool(value.add)
        for v in value.add or []:
            obj, data = _parse_data(info, manager.model, v)
            if obj and data:
                manager.add(obj, **data)
            elif obj:
                # Do this later in a bulk
                to_add.append(obj)
            elif data:
                manager.create(**data)
            else:
                raise AssertionError

        need_remove_cache = need_remove_cache or bool(value.remove)
        for v in value.remove or []:
            obj, data = _parse_data(info, manager.model, v)
            assert not data
            to_remove.append(obj)

    if to_add:
        manager.add(*to_add)
    if to_remove:
        manager.remove(*to_remove)
    if to_delete:
        manager.filter(pk__in=[item.pk for item in to_delete]).delete()

    if need_remove_cache:
        manager._remove_prefetched_objects()  # type:ignore
