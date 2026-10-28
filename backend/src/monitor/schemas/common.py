"""Strict, reusable Pydantic wire types and credential-safe error messages."""

from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    FailFast,
    Field,
    StrictFloat,
    StrictInt,
    TypeAdapter,
    ValidationError,
    WrapValidator,
    model_validator,
)
from pydantic_core import PydanticCustomError


def invalid(message):
    raise PydanticCustomError("wire_value", message)


def described(annotation, message):
    def validate(value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            # A nested model's more specific error must not become a list error.
            error = exc.errors(include_input=False, include_url=False)[0]
            if error["type"] == "wire_value":
                raise
            invalid(message)

    return Annotated[
        annotation,
        WrapValidator(validate),
        Field(json_schema_extra={"x-error": message}),
    ]


def text(field, limit, *, nullable=False, minimum=0):
    annotation = Annotated[
        str, Field(strict=True, min_length=minimum, max_length=limit)
    ]
    if nullable:
        annotation = annotation | None
    return described(annotation, f"无效的字符串字段：{field}")


def number(field, maximum, *, nullable=True):
    def json_number(value):
        # StrictFloat still accepts objects implementing __float__ in Python.
        # Match JSON numeric types, including in internal/service invocations.
        if type(value) not in (int, float) and not (value is None and nullable):
            invalid(f"无效的数值字段：{field}")
        # Check the original JSON number before union validation: a rejected
        # integer can otherwise take StrictFloat's int-accepting branch and round
        # a >2**53 value down into the allowed range. NaN/Inf also fail this test.
        if value is not None and not 0 <= value <= maximum:
            invalid(f"无效的数值字段：{field}")
        return value

    # Constrain each numeric branch. Pydantic's generic constraints on a union
    # otherwise export nonstandard "ge"/"le" keys rather than JSON Schema bounds.
    annotation = (
        Annotated[StrictInt, Field(ge=0, le=maximum)]
        | Annotated[StrictFloat, Field(ge=0, le=maximum, allow_inf_nan=False)]
    )
    if nullable:
        annotation = annotation | None
    return described(
        Annotated[annotation, BeforeValidator(json_number)], f"无效的数值字段：{field}"
    )


def array(item, field, maximum):
    return described(
        Annotated[list[item], Field(strict=True, max_length=maximum), FailFast()],
        f"无效的列表字段：{field}",
    )


def integer(message, minimum, maximum):
    return described(
        Annotated[int, Field(strict=True, ge=minimum, le=maximum)], message
    )


class WireModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="wrap")
    @classmethod
    def public_errors(cls, value, handler):
        try:
            return handler(value)
        except ValidationError as exc:
            error = exc.errors(include_input=False, include_url=False)[0]
            if error["type"] == "wire_value":
                invalid(error["msg"])
            field = cls.model_fields.get(error["loc"][0]) if error["loc"] else None
            metadata = field.json_schema_extra if field else None
            invalid((metadata or {}).get("x-error", "无效的 JSON 对象"))


def validate(model, value, **kwargs):
    """Compatibility boundary: ValueError contains a message, never raw inputs."""
    try:
        return model.model_validate(value, **kwargs)
    except ValidationError as exc:
        raise ValueError(
            exc.errors(include_input=False, include_url=False)[0]["msg"]
        ) from None


Token = Annotated[
    str, Field(strict=True, min_length=16, max_length=256, pattern=r"^[!-~]+$")
]
TOKEN_ADAPTER = TypeAdapter(Token)


def valid_token(value):
    try:
        TOKEN_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        return False
    return True


def version(message):
    # Literal[1] alone accepts True and 1.0. Strict integer + bounds does not.
    return integer(message, 1, 1)
