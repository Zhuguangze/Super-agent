"""工具参数校验——JSON Schema 常用子集的轻量校验（v1 不引第三方依赖）。

支持：type（object 及基本类型）、properties、required、enum、items。
校验失败返回人类可读的错误串；模型生成的 input 由 API 保证按 schema 约束，这里是防御层。
"""

SchemaType = str | list[str]

_PRIMITIVE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
}


def validate_params(params: dict, schema: dict) -> str | None:
    """按 JSON Schema 校验参数；返回 None 表示通过，否则返回错误描述。"""
    if schema.get("type") not in (None, "object"):
        return "工具参数 schema 顶层必须是 object"
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        if key not in params:
            return f"缺少必填参数: {key}"
    for key, value in params.items():
        if key not in properties:
            continue  # 未声明的字段不拦截（additionalProperties 校验留后续）
        error = _validate_value(key, value, properties[key])
        if error:
            return error
    return None


def _validate_value(key: str, value, schema: dict) -> str | None:
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, t) for t in types):
            return f"参数 {key} 类型应为 {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"参数 {key} 必须是 {schema['enum']} 之一"
    if _type_matches(value, "array") and "items" in schema:
        for i, item in enumerate(value):
            error = _validate_value(f"{key}[{i}]", item, schema["items"])
            if error:
                return error
    return None


def _type_matches(value, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    # bool 是 int 的子类——integer 检查须排除 bool
    checks = _PRIMITIVE_CHECKS.get(type_name)
    if checks is None:
        return True
    if isinstance(value, bool) and bool not in checks:
        return False
    return isinstance(value, checks)
