"""Desk UI forms."""
from django import forms

from assistant.models import IntegrationSettings
from assistant.services.runtime_settings import SECRET_FIELDS

_SECRET_PLACEHOLDER = "Saved — leave blank to keep"


class IntegrationSettingsForm(forms.ModelForm):
    """Integration credentials form for ``/settings/``.

    Non-secret fields: blank ⇒ fall back to the matching environment variable.
    Secret fields: never rendered back to the browser. Blank keeps the stored
    secret, and a per-field checkbox clears it so `.env` takes over again.
    """

    class Meta:
        model = IntegrationSettings
        fields = [
            "openrouter_api_key",
            "odoo_url",
            "odoo_db",
            "odoo_username",
            "odoo_api_key",
            "dev_mode",
            "twilio_account_sid",
            "twilio_auth_token",
            "twilio_whatsapp_from",
        ]
        widgets = {
            "odoo_url": forms.TextInput(
                attrs={"class": "input", "placeholder": "https://your-company.odoo.com",
                       "dir": "ltr"},
            ),
            "odoo_db": forms.TextInput(
                attrs={"class": "input", "placeholder": "your-database-name", "dir": "ltr"},
            ),
            "odoo_username": forms.TextInput(
                attrs={"class": "input", "placeholder": "you@company.com", "dir": "ltr"},
            ),
            "twilio_account_sid": forms.TextInput(
                attrs={"class": "input", "placeholder": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                       "dir": "ltr"},
            ),
            "twilio_whatsapp_from": forms.TextInput(
                attrs={"class": "input", "placeholder": "whatsapp:+14155238886", "dir": "ltr"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Secrets are write-only: empty input, masked placeholder, plus a
        # "clear" checkbox so a tester can fall back to .env again.
        for name in SECRET_FIELDS:
            field = self.fields[name]
            field.required = False
            field.widget = forms.PasswordInput(
                attrs={
                    "class": "input",
                    "autocomplete": "new-password",
                    "spellcheck": "false",
                    "placeholder": _SECRET_PLACEHOLDER,
                },
                render_value=False,
            )
            field.help_text = (
                "Leave blank to keep the saved value — tick the box to fall back to .env."
            )
            self.initial[name] = ""
            self.fields[f"clear_{name}"] = forms.BooleanField(
                required=False,
                label="Use the .env value instead",
                widget=forms.CheckboxInput(
                    attrs={"class": "h-4 w-4 shrink-0 accent-[#ea0f16]"}
                ),
            )

        # dev_mode is a tri-state NullBooleanSelect — give it the panel style.
        for field in self.fields.values():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs["class"] = "select"

    def clean(self):
        cleaned = super().clean()
        for name in SECRET_FIELDS:
            if cleaned.get(f"clear_{name}"):
                cleaned[name] = ""
            elif not cleaned.get(name) and self.instance.pk:
                # Blank + already saved ⇒ keep the stored secret untouched.
                cleaned[name] = getattr(self.instance, name, "")
        return cleaned

    def clean_odoo_url(self):
        url = (self.cleaned_data.get("odoo_url") or "").strip()
        return url.rstrip("/")

    def clean_twilio_whatsapp_from(self):
        sender = (self.cleaned_data.get("twilio_whatsapp_from") or "").strip()
        if sender and not sender.startswith("whatsapp:"):
            sender = f"whatsapp:{sender}"
        return sender

