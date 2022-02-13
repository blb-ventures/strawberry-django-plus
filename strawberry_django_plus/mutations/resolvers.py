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
from strawberry.arguments import UNSET, is_unset
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
    from django.db.models.manager import RelatedManager

_T = TypeVar("_T")
_M = TypeVar("_M", bound=Model)
_InputListTypes: TypeAlias = Union[strawberry.ID, "ParsedObject"]


def _parse_pk(
    value: Optional[Union["ParsedObject", strawberry.ID, Model]],
    model: Type[_M],
) -> Tuple[Optional[_M], Optional[Dict[str, Any]]]:
    if value is None:
        return None, None
    elif isinstance(value, ParsedObject):
        return value.parse(model)
    elif isinstance(value, dict):
        return None, value

    return model._default_manager.get(pk=value), None


@dataclasses.dataclass
class ParsedObject:
    pk: Optional[Union[strawberry.ID, Model]]
    data: Optional[Dict[str, Any]] = None

    def parse(self, model: Type[_M]) -> Tuple[Optional[_M], Optional[Dict[str, Any]]]:
        if self.pk is None:
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
    data: Optional[Dict[str, Any]] = None


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
def parse_input(info: Info, data: _T) -> _T:
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
        pk = parse_input(info, getattr(data, "id", UNSET))
        data = parse_input(info, dataclasses.asdict(data))
        data.pop("id", None)
        return ParsedObject(
            pk=pk,
            data=data if len(data) else None,
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
            data=parse_input(info, d),
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
    full_clean: bool = True,
) -> _M:
    ...


@overload
def create(
    info: Info,
    model: Type[_M],
    data: List[Dict[str, Any]],
    *,
    full_clean: bool = True,
) -> List[_M]:
    ...


@transaction.atomic
def create(
    info,
    model,
    data,
    *,
    full_clean=True,
):
    if isinstance(data, list):
        return [create(info, model, d, full_clean=full_clean) for d in data]
    elif dataclasses.is_dataclass(data):
        data = vars(data)
    return update(info, model(), data, full_clean=full_clean)


@overload
def update(
    info: Info,
    instance: _M,
    data: Dict[str, Any],
    *,
    full_clean: bool = True,
) -> _M:
    ...


@overload
def update(
    info: Info,
    instance: Iterable[_M],
    data: Dict[str, Any],
    *,
    full_clean: bool = True,
) -> List[_M]:
    ...


@transaction.atomic
def update(info, instance, data, *, full_clean=True):
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
    files: List[Tuple[models.FileField, Union[Upload, Literal[False]]]] = []
    m2m: List[Tuple[Union[ManyToManyField, ForeignObjectRel], Any]] = []

    if dataclasses.is_dataclass(data):
        data = vars(data)

    for name, value in parse_input(info, data).items():
        field = fields.get(name)
        if field is None:
            continue

        if is_unset(value):
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

        for instance in instances:
            update_field(info, instance, field, value)

    for instance in instances:
        for file_field, value in files:
            file_field.save_form_data(instance, value)

        if full_clean:
            instance.full_clean()

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
    if is_unset(value):
        return

    data = None
    if value and isinstance(field, models.ForeignObject) and not isinstance(value, Model):
        value, data = _parse_pk(value, field.related_model)

    field.save_form_data(instance, value)
    # If data was passed to the foreign key, update it recursively
    if data:
        update(info, value, data)


def update_m2m(
    info: Info,
    instance: Model,
    field: Union[ManyToManyField, ForeignObjectRel],
    value: Any,
):
    if is_unset(value):
        return

    if isinstance(field, OneToOneRel):
        remote_field = field.remote_field
        value, data = _parse_pk(value, remote_field.model)
        remote_field.save_form_data(value, instance)
        value.save()

        # If data was passed to the field, update it recursively
        if data:
            update(info, value, data)
        return

    extras = {}
    if isinstance(field, ManyToManyField):
        manager = cast("RelatedManager", getattr(instance, field.attname))
        extras["through_defaults"] = value.data if isinstance(value, ParsedObjectList) else None
    else:
        assert isinstance(field, (ManyToManyRel, ManyToOneRel))
        accessor_name = field.get_accessor_name()
        assert accessor_name
        manager = cast("RelatedManager", getattr(instance, accessor_name))

    values = value.set if isinstance(value, ParsedObjectList) else value
    if isinstance(values, list):
        if isinstance(value, ParsedObjectList) and getattr(value, "add", None):
            raise ValueError("'add' cannot be used together with 'set'")
        if isinstance(value, ParsedObjectList) and getattr(value, "remove", None):
            raise ValueError("'remove' cannot be used together with 'set'")

        parsed = []
        for v in values:
            obj, data = _parse_pk(v, manager.model)
            if obj:
                if data is not None:
                    update(info, obj, data)
                parsed.append(obj)
            elif data:
                parsed.append(manager.create(**data))

        if parsed:
            manager.set(parsed, **extras)
        else:
            manager.clear()
    else:
        if value.add:
            parsed = []
            for v in value.add:
                obj, data = _parse_pk(v, manager.model)
                if obj:
                    if data is not None:
                        update(info, obj, data)
                    parsed.append(obj)
                elif data:
                    parsed.append(manager.create(**data))
            manager.add(*parsed, **extras)

        if value.remove:
            parsed = []
            for v in value.remove:
                obj, data = _parse_pk(v, manager.model)
                assert obj
                if data is not None:
                    update(info, obj, data)
                parsed.append(obj)
            manager.remove(*parsed)
