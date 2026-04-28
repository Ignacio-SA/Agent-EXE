"""
franchise_resolver.py
---------------------
Detecta para qué franquicia(s) aplica cada consulta del usuario.

resolve() retorna:
  - franchise_codes: list[str] con los códigos resueltos (None = pedir aclaración)
  - clarification:   texto a devolver al usuario si la consulta es ambigua
  - is_franchise_compare: True cuando el usuario quiere comparar franquicias entre sí
    (solo aplica cuando agent_type == "comparative")

Lógica:
  1. Si solo hay una franquicia configurada → sin ambigüedad, retorna esa.
  2. Contexto con pregunta de aclaración previa → matching ordinal/cardinal.
  3. Si el mensaje menciona todas las franquicias o keyword de comparación → todas.
  4. Si el mensaje menciona solo un label → retorna ese.
  5. Si el mensaje es follow-up y el contexto de memoria tiene una franquicia → la reutiliza.
  6. Si el mensaje es follow-up y la sesión tiene franquicia guardada → la reutiliza.
  7. Si sigue siendo ambiguo → retorna clarification con las opciones.

Nota: los pasos 5 y 6 solo aplican a mensajes de follow-up. Las consultas nuevas
(con keywords de fecha o longitud > 60 chars) siempre piden aclaración cuando
la franquicia es ambigua.
"""

import logging
import re

_log = logging.getLogger(__name__)

# Palabras ordinales/cardinales por posición (1-indexed, soporta hasta 5 franquicias)
_ORDINAL_SETS = [
    {"1", "uno", "una", "primera", "primero", "primer"},
    {"2", "dos", "segunda", "segundo"},
    {"3", "tres", "tercera", "tercero"},
    {"4", "cuatro", "cuarta", "cuarto"},
    {"5", "cinco", "quinta", "quinto"},
]

_COMPARE_KEYWORDS = [
    "ambas", "las dos", "ambas franquicias", "las dos franquicias",
    "entre franquicias", "comparar franquicias", "compará franquicias",
    "todas las franquicias", "todas franquicias",
]

_CLARIFICATION_MARKERS = [
    "¿para cuál franquicia necesitás",
    "para cuál franquicia necesitás",
    "para cual franquicia necesitas",
    "necesitás los datos? (",
    "necesitas los datos? (",
]

# Keywords que indican que el mensaje referencia un período → consulta nueva
_DATE_KEYWORDS = [
    "hoy", "ayer", "semana", "mes", "año",
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    "al ", "desde", "hasta",
]

def _is_new_query(msg: str) -> bool:
    """True si el mensaje contiene palabras de fecha → posible consulta nueva."""
    return any(k in msg for k in _DATE_KEYWORDS)


class FranchiseResolver:

    def resolve(
        self,
        user_message: str,
        context: str,
        franchise_map: dict[str, str],   # {code: label}
        agent_type: str,
        session_franchise: list[str] | None = None,
    ) -> tuple[list[str] | None, str, bool]:
        """
        Retorna (franchise_codes, clarification_text, is_franchise_compare).

        franchise_codes:      lista de códigos a consultar; None significa pedir aclaración.
        clarification_text:   pregunta al usuario si es ambiguo (vacío si no).
        is_franchise_compare: True solo cuando agent_type=="comparative" y el usuario
                              quiere comparar las franquicias entre sí.
        """
        if len(franchise_map) <= 1:
            return list(franchise_map.keys()), "", False

        codes        = list(franchise_map.keys())
        labels       = list(franchise_map.values())
        labels_lower = [l.lower() for l in labels]
        n            = len(codes)
        msg          = user_message.lower().strip()
        words        = set(re.findall(r'\w+', msg))

        # ── Follow-up a pregunta de aclaración de franquicia ─────────────
        ctx_lower = context.lower() if context else ""
        context_has_clarification = any(m in ctx_lower for m in _CLARIFICATION_MARKERS)

        if context_has_clarification:
            # "ambas" / "todas" / "las dos"
            both_words = {"ambas", "todas", "ambos"}
            if words & both_words or ("las" in words and "dos" in words):
                is_compare = agent_type == "comparative"
                _log.info("[FranchiseResolver] Follow-up: ambas franquicias")
                return codes, "", is_compare

            # Ordinales por posición
            for i, ordinals in enumerate(_ORDINAL_SETS):
                if i >= n:
                    break
                if words & ordinals:
                    _log.info(
                        "[FranchiseResolver] Follow-up ordinal: posición %d → %s",
                        i + 1, labels[i],
                    )
                    return [codes[i]], "", False

        # ── Compare keywords / todas las franquicias ─────────────────────
        compare_keyword_found = any(k in msg for k in _COMPARE_KEYWORDS)
        matched_indices = [i for i, l in enumerate(labels_lower) if l in msg]
        all_matched = len(matched_indices) == n

        if all_matched or compare_keyword_found:
            is_compare = agent_type == "comparative"
            _log.info("[FranchiseResolver] Todas las franquicias — compare=%s", is_compare)
            return codes, "", is_compare

        # ── Franquicia específica por label ──────────────────────────────
        if len(matched_indices) == 1:
            i = matched_indices[0]
            _log.info("[FranchiseResolver] Franquicia por label: %s", labels[i])
            return [codes[i]], "", False

        # ── Contexto de memoria: solo para follow-ups (previene inferencia de contexto viejo) ──
        if not _is_new_query(msg) and ctx_lower:
            ctx_matched = [i for i, l in enumerate(labels_lower) if l in ctx_lower]
            if len(ctx_matched) == 1:
                i = ctx_matched[0]
                _log.info("[FranchiseResolver] Franquicia inferida del contexto: %s", labels[i])
                return [codes[i]], "", False

        # ── Sesión (franquicia "sticky"): siempre disponible como último recurso ──
        if session_franchise and agent_type in ("data", "comparative"):
            labels_sess = [franchise_map.get(c, c) for c in session_franchise]
            _log.info("[FranchiseResolver] Reutilizando franquicia de sesión: %s", labels_sess)
            return session_franchise, "", False

        # ── Ambiguo: pedir aclaración (solo para consultas de datos) ─────
        if agent_type in ("data", "comparative"):
            options = " o ".join(labels)
            clarification = (
                f"¿Para cuál franquicia necesitás los datos? ({options})\n"
                f"También podés pedirme datos de ambas si querés verlas juntas."
            )
            _log.info("[FranchiseResolver] Consulta ambigua — solicitando aclaración")
            return None, clarification, False

        return codes, "", False


franchise_resolver = FranchiseResolver()
