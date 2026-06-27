from django import template

register = template.Library()


@register.filter
def get(mapping, key):
    """Dict lookup by a variable key inside templates: {{ mydict|get:somekey }}."""
    if mapping is None:
        return None
    return mapping.get(key)
