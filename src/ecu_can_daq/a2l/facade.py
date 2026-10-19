"""A2L facade using pya2l (0.4.x) with fallback to legacy parser."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging

from ecu_can_daq.models import (
    ByteOrder,
    DataType,
    CompuMethod,
    CompuMethodType,
    MeasurementDefinition,
    CharacteristicDefinition,
    CharacteristicType,
    MemorySegment,
)

log = logging.getLogger(__name__)


class A2LProject:
    def __init__(self, a2l_path: Path):
        self.a2l_path = a2l_path
        self.measurements: dict[str, MeasurementDefinition] = {}
        self.characteristics: dict[str, CharacteristicDefinition] = {}
        self.compu_methods: dict[str, CompuMethod] = {}
        self.memory_segments: list[MemorySegment] = []
        self.modules: list[str] = []
        self.raw_project: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "a2l_file": str(self.a2l_path),
            "measurements": len(self.measurements),
            "characteristics": len(self.characteristics),
            "compu_methods": len(self.compu_methods),
            "modules": self.modules,
        }


def _map_byte_order(v: Any) -> ByteOrder:
    if not v:
        return ByteOrder.LITTLE
    s = str(v).upper()
    if "MSB_FIRST" in s or "BIG" in s:
        return ByteOrder.BIG
    return ByteOrder.LITTLE


def _map_data_type(v: Any) -> DataType | None:
    try:
        txt = str(v).strip().upper()
        if "." in txt:
            txt = txt.split(".")[-1]
        # Remove quotes
        txt = txt.strip('"').strip("'")
        return DataType.from_a2l(txt)
    except Exception:
        return None


def _get_nested_value(obj: Any, *attrs: str, default: Any = None) -> Any:
    """Try to get nested attribute Value or Source etc."""
    cur = obj
    try:
        for attr in attrs:
            if cur is None:
                return default
            # protobuf messages may have attribute lower or upper?
            # Try direct
            if hasattr(cur, attr):
                cur = getattr(cur, attr)
            else:
                # case insensitive?
                found = None
                for a in dir(cur):
                    if a.lower() == attr.lower():
                        found = getattr(cur, a)
                        break
                if found is None:
                    return default
                cur = found
        # If cur is a wrapper with Value, get Value
        if hasattr(cur, "Value"):
            # Value may be int or string
            v = getattr(cur, "Value")
            # Could be message with Value?
            if hasattr(v, "Value"):
                return getattr(v, "Value")
            return v
        if hasattr(cur, "value"):
            return getattr(cur, "value")
        return cur
    except Exception:
        return default


def _extract_meas_string(meas: Any, field: str) -> str:
    # Try field direct .Value.Value etc.
    # field like "Name"
    try:
        # Example: meas.Name.Value
        if hasattr(meas, field):
            sub = getattr(meas, field)
            if hasattr(sub, "Value"):
                val = getattr(sub, "Value")
                # val may be string or wrapped
                if isinstance(val, str):
                    return val
                if hasattr(val, "Value"):
                    return str(getattr(val, "Value"))
                return str(val)
            # For DataType
            if hasattr(sub, "Value"):
                return str(sub.Value)
            return str(sub)
    except Exception:
        pass
    # Try lower case
    try:
        low = field.lower()
        if hasattr(meas, low):
            sub = getattr(meas, low)
            if hasattr(sub, "Value"):
                return str(sub.Value)
            return str(sub)
    except Exception:
        pass
    return ""


def _extract_address(meas: Any) -> tuple[int, int]:
    """Returns (address, extension)"""
    addr = 0
    ext = 0
    try:
        if hasattr(meas, "ECU_ADDRESS"):
            ecu = getattr(meas, "ECU_ADDRESS")
            # ecu.Address.Value
            if hasattr(ecu, "Address"):
                a = getattr(ecu, "Address")
                if hasattr(a, "Value"):
                    v = getattr(a, "Value")
                    # v may be string like "4096"
                    try:
                        addr = int(v)
                    except Exception:
                        try:
                            addr = int(str(v), 0)
                        except Exception:
                            addr = 0
        if hasattr(meas, "ECU_ADDRESS_EXTENSION"):
            ecu_ext = getattr(meas, "ECU_ADDRESS_EXTENSION")
            if hasattr(ecu_ext, "Extension"):
                e = getattr(ecu_ext, "Extension")
                # Extension may have Value? In sample Extension.Base 10 Size 1 but no Value? Means 0?
                # Try to get Value attr
                if hasattr(e, "Value"):
                    try:
                        ext = int(e.Value)
                    except Exception:
                        ext = 0
                elif hasattr(ecu_ext, "Extension"):
                    # fallback: try to get from Extension itself? It may have Base etc
                    pass
    except Exception:
        pass
    return addr, ext


def _extract_limits(meas: Any) -> tuple[float, float]:
    lower = 0.0
    upper = 100.0
    try:
        if hasattr(meas, "LowerLimit"):
            ll = getattr(meas, "LowerLimit")
            val = None
            # Direct Value
            if hasattr(ll, "Value"):
                try:
                    v = getattr(ll, "Value")
                    # v could be int/float or message
                    if isinstance(v, (int, float)):
                        val = float(v)
                    elif hasattr(v, "Value"):
                        try:
                            val = float(v.Value)
                        except Exception:
                            pass
                    else:
                        try:
                            val = float(v)
                        except Exception:
                            pass
                except Exception:
                    pass
            if val is None and hasattr(ll, "Source"):
                try:
                    src = getattr(ll, "Source")
                    if src not in (None, ""):
                        val = float(src)
                except Exception:
                    pass
            if val is not None:
                lower = val

        if hasattr(meas, "UpperLimit"):
            ul = getattr(meas, "UpperLimit")
            val = None
            if hasattr(ul, "Value"):
                try:
                    v = getattr(ul, "Value")
                    if isinstance(v, (int, float)):
                        val = float(v)
                    elif hasattr(v, "Value"):
                        val = float(v.Value)
                    else:
                        val = float(v)
                except Exception:
                    pass
            if val is None and hasattr(ul, "Source"):
                try:
                    src = getattr(ul, "Source")
                    if src not in (None, ""):
                        val = float(src)
                except Exception:
                    pass
            if val is not None:
                upper = val
    except Exception:
        pass
    return lower, upper


def _extract_byte_order(meas: Any) -> ByteOrder:
    try:
        if hasattr(meas, "BYTE_ORDER"):
            bo = getattr(meas, "BYTE_ORDER")
            if hasattr(bo, "ByteOrder"):
                return _map_byte_order(bo.ByteOrder)
        if hasattr(meas, "ByteOrder"):
            bo = getattr(meas, "ByteOrder")
            return _map_byte_order(bo)
    except Exception:
        pass
    return ByteOrder.LITTLE


def _extract_conversion(meas: Any) -> str:
    try:
        if hasattr(meas, "Conversion"):
            conv = getattr(meas, "Conversion")
            if hasattr(conv, "Value"):
                return str(conv.Value)
            return str(conv)
    except Exception:
        pass
    return ""


def _parse_pya2l_tree(tree: Any, a2l_path: Path) -> A2LProject:
    proj = A2LProject(a2l_path=a2l_path)
    proj.raw_project = tree

    # Tree is RootNodeType with PROJECT field
    project_msg = None
    if hasattr(tree, "PROJECT"):
        project_msg = getattr(tree, "PROJECT")
    elif hasattr(tree, "project"):
        project_msg = getattr(tree, "project")

    if project_msg is None:
        # maybe tree itself is project?
        project_msg = tree

    modules: list[Any] = []
    if hasattr(project_msg, "MODULE"):
        modules = list(getattr(project_msg, "MODULE"))
    elif hasattr(project_msg, "module"):
        modules = list(getattr(project_msg, "module"))
    elif hasattr(project_msg, "modules"):
        modules = list(getattr(project_msg, "modules"))

    for mod in modules:
        mod_name = ""
        try:
            if hasattr(mod, "Name"):
                n = getattr(mod, "Name")
                if hasattr(n, "Value"):
                    mod_name = str(n.Value)
                else:
                    mod_name = str(n)
        except Exception:
            mod_name = "Unknown"
        proj.modules.append(mod_name)

        # COMPU_METHODS
        compu_list: list[Any] = []
        if hasattr(mod, "COMPU_METHOD"):
            compu_list = list(getattr(mod, "COMPU_METHOD"))
        elif hasattr(mod, "compu_method"):
            compu_list = list(getattr(mod, "compu_method"))

        for cm in compu_list:
            try:
                cm_name = ""
                if hasattr(cm, "Name"):
                    cm_name = getattr(cm, "Name").Value if hasattr(getattr(cm, "Name"), "Value") else str(getattr(cm, "Name"))
                if not cm_name:
                    continue
                # ConversionType
                ctype_str = "IDENTICAL"
                if hasattr(cm, "ConversionType"):
                    ct = getattr(cm, "ConversionType")
                    # may be ConversionType enum?
                    if hasattr(ct, "ConversionType"):
                        ctype_str = str(ct.ConversionType)
                    else:
                        ctype_str = str(ct)
                    # Clean
                    ctype_str = ctype_str.split(".")[-1].upper()
                try:
                    cm_type = CompuMethodType(ctype_str)
                except ValueError:
                    cm_type = CompuMethodType.IDENTICAL

                fmt = "%7.3"
                if hasattr(cm, "Format"):
                    f = getattr(cm, "Format")
                    fmt = getattr(f, "Value", str(f)) if hasattr(f, "Value") else str(f)

                unit = ""
                if hasattr(cm, "Unit"):
                    u = getattr(cm, "Unit")
                    unit = getattr(u, "Value", str(u)) if hasattr(u, "Value") else str(u)
                    unit = str(unit).strip('"').strip("'")

                coeffs = (0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
                if hasattr(cm, "COEFFS"):
                    coeff_msg = getattr(cm, "COEFFS")
                    try:
                        # COEFFS has a,b,c,d,e,f
                        a = getattr(coeff_msg, "a", 0.0)
                        b = getattr(coeff_msg, "b", 1.0)
                        c = getattr(coeff_msg, "c", 0.0)
                        d = getattr(coeff_msg, "d", 0.0)
                        e = getattr(coeff_msg, "e", 0.0)
                        f = getattr(coeff_msg, "f", 1.0)
                        # If they are wrapper messages with Value?
                        def _unwrap(v):
                            if hasattr(v, "Value"):
                                vv = getattr(v, "Value")
                                return float(vv) if isinstance(vv, (int, float, str)) else float(str(vv))
                            try:
                                return float(v)
                            except Exception:
                                return 0.0
                        coeffs = (_unwrap(a), _unwrap(b), _unwrap(c), _unwrap(d), _unwrap(e), _unwrap(f))
                    except Exception:
                        pass

                proj.compu_methods[cm_name] = CompuMethod(
                    name=cm_name,
                    compu_type=cm_type,
                    unit=unit,
                    format_str=str(fmt),
                    coeffs=coeffs,
                )
            except Exception as e:
                log.debug(f"Failed parse compu {e}")
                continue

        # MEASUREMENTS
        meas_list: list[Any] = []
        if hasattr(mod, "MEASUREMENT"):
            meas_list = list(getattr(mod, "MEASUREMENT"))
        elif hasattr(mod, "measurement"):
            meas_list = list(getattr(mod, "measurement"))

        for meas in meas_list:
            try:
                name = ""
                if hasattr(meas, "Name"):
                    n = getattr(meas, "Name")
                    name = getattr(n, "Value", str(n)) if hasattr(n, "Value") else str(n)
                if not name:
                    continue

                dt_str = ""
                if hasattr(meas, "DataType"):
                    dt_msg = getattr(meas, "DataType")
                    dt_str = getattr(dt_msg, "Value", str(dt_msg)) if hasattr(dt_msg, "Value") else str(dt_msg)
                dt = _map_data_type(dt_str) or DataType.ULONG

                addr, ext = _extract_address(meas)
                lower, upper = _extract_limits(meas)
                bo = _extract_byte_order(meas)
                conv_ref = _extract_conversion(meas)

                cm_ref = proj.compu_methods.get(conv_ref)
                unit = cm_ref.unit if cm_ref else ""
                # Description
                desc = ""
                if hasattr(meas, "LongIdentifier"):
                    li = getattr(meas, "LongIdentifier")
                    desc = getattr(li, "Value", str(li)) if hasattr(li, "Value") else str(li)

                md = MeasurementDefinition(
                    name=name,
                    data_type=dt,
                    ecu_address=addr,
                    lower_limit=lower,
                    upper_limit=upper,
                    address_extension=ext,
                    byte_order=bo,
                    compu_method=cm_ref,
                    unit=unit,
                    description=str(desc)[:500],
                )
                proj.measurements[name] = md
            except Exception as e:
                log.debug(f"Failed parse measurement: {e}")
                continue

        # CHARACTERISTICS
        char_list: list[Any] = []
        if hasattr(mod, "CHARACTERISTIC"):
            char_list = list(getattr(mod, "CHARACTERISTIC"))
        elif hasattr(mod, "characteristic"):
            char_list = list(getattr(mod, "characteristic"))

        for char in char_list:
            try:
                name = ""
                if hasattr(char, "Name"):
                    n = getattr(char, "Name")
                    name = getattr(n, "Value", str(n)) if hasattr(n, "Value") else str(n)
                if not name:
                    continue

                # Type
                type_str = "VALUE"
                if hasattr(char, "Type"):
                    t = getattr(char, "Type")
                    type_str = getattr(t, "Value", str(t)) if hasattr(t, "Value") else str(t)
                    type_str = str(type_str).split(".")[-1].upper()
                try:
                    ctype = CharacteristicType(type_str)
                except ValueError:
                    ctype = CharacteristicType.VALUE

                dt_str = ""
                if hasattr(char, "DataType"):
                    dt_msg = getattr(char, "DataType")
                    dt_str = getattr(dt_msg, "Value", str(dt_msg)) if hasattr(dt_msg, "Value") else str(dt_msg)
                dt = _map_data_type(dt_str) or DataType.ULONG

                addr, ext = _extract_address(char)
                lower, upper = _extract_limits(char)
                bo = _extract_byte_order(char)
                conv_ref = _extract_conversion(char)
                cm_ref = proj.compu_methods.get(conv_ref)
                unit = cm_ref.unit if cm_ref else ""
                desc = ""
                if hasattr(char, "LongIdentifier"):
                    li = getattr(char, "LongIdentifier")
                    desc = getattr(li, "Value", str(li)) if hasattr(li, "Value") else str(li)

                cd = CharacteristicDefinition(
                    name=name,
                    char_type=ctype,
                    data_type=dt,
                    ecu_address=addr,
                    lower_limit=lower,
                    upper_limit=upper,
                    address_extension=ext,
                    byte_order=bo,
                    compu_method=cm_ref,
                    unit=unit,
                    description=str(desc)[:500],
                )
                proj.characteristics[name] = cd
            except Exception as e:
                log.debug(f"Failed parse characteristic: {e}")
                continue

    return proj


def _try_load_with_pya2l(path: Path) -> Any:
    """Load with pya2l A2lParser."""
    try:
        import pya2l.parser
    except ImportError as e:
        raise RuntimeError(f"pya2l not installed: {e}")

    parser = pya2l.parser.A2lParser()
    try:
        data = path.read_bytes()
        tree = parser.tree_from_a2l(data)
        return tree
    finally:
        try:
            parser.close()
        except Exception:
            pass


def load_a2l_project(a2l_path: str | Path) -> A2LProject:
    path = Path(a2l_path)
    if not path.exists():
        raise FileNotFoundError(f"A2L file not found: {path}")

    from ecu_can_daq.legacy_a2l import load_measurements as legacy_load

    try:
        legacy_meas = legacy_load(path)
    except Exception:
        legacy_meas = {}

    try:
        raw = _try_load_with_pya2l(path)
        proj = _parse_pya2l_tree(raw, path)
        if not proj.measurements and legacy_meas:
            proj.measurements = legacy_meas
        # Merge legacy not in pya2l
        for name, lm in legacy_meas.items():
            if name not in proj.measurements:
                proj.measurements[name] = lm
        return proj
    except Exception as e:
        log.warning(f"pya2l parsing failed for {path}: {e}, falling back to legacy parser")
        proj = A2LProject(a2l_path=path)
        proj.measurements = legacy_meas
        return proj


def resolve_measurements(definitions: dict[str, MeasurementDefinition], names: list[str]) -> list[MeasurementDefinition]:
    missing = [n for n in names if n not in definitions]
    if missing:
        raise KeyError(f"Measurements not found in A2L file: {', '.join(sorted(missing))}")
    return [definitions[n] for n in names]
