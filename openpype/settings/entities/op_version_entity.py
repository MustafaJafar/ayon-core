from openpype.lib.openpype_version import (
    get_remote_versions,
    get_OpenPypeVersion,
    get_installed_version
)
from .input_entities import TextEntity
from .lib import (
    OverrideState,
    NOT_SET
)
from .exceptions import BaseInvalidValue


class OpenPypeVersionInput(TextEntity):
    """Entity to store OpenPype version to use.

    It is text input as creating of settings on different machines may
    affect which versions are available so it must have option to set OpenPype
    version which is not available for machine where settings entities are
    loaded.

    It is possible to enter empty string. In that case is used any latest
    version. Any other string must match regex of OpenPype version semantic.
    """
    def _item_initialization(self):
        super(OpenPypeVersionInput, self)._item_initialization()
        self.multiline = False
        self.placeholder_text = "Latest"
        self.value_hints = []

    def _get_openpype_versions(self):
        """This is abstract method returning version hints for UI purposes."""
        raise NotImplementedError((
            "{} does not have implemented '_get_openpype_versions'"
        ).format(self.__class__.__name__))

    def set_override_state(self, state, *args, **kwargs):
        """Update value hints for UI purposes."""
        value_hints = []
        if state is OverrideState.STUDIO:
            versions = self._get_openpype_versions()
            if versions is not None:
                for version in versions:
                    value_hints.append(str(version))

        self.value_hints = value_hints

        super(OpenPypeVersionInput, self).set_override_state(
            state, *args, **kwargs
        )

    def convert_to_valid_type(self, value):
        """Add validation of version regex."""
        if value and value is not NOT_SET:
            OpenPypeVersion = get_OpenPypeVersion()
            if OpenPypeVersion is not None:
                try:
                    OpenPypeVersion(version=value)
                except Exception:
                    raise BaseInvalidValue(
                        "Value \"{}\"is not valid version format.".format(
                            value
                        ),
                        self.path
                    )
        return super(OpenPypeVersionInput, self).convert_to_valid_type(value)


class ProductionVersionsInputEntity(OpenPypeVersionInput):
    """Entity meant only for global settings to define production version."""
    schema_types = ["production-versions-text"]

    def _get_openpype_versions(self):
        versions = get_remote_versions(staging=False, production=True)
        versions.append(get_installed_version())
        return sorted(versions)


class StagingVersionsInputEntity(OpenPypeVersionInput):
    """Entity meant only for global settings to define staging version."""
    schema_types = ["staging-versions-text"]

    def _get_openpype_versions(self):
        versions = get_remote_versions(staging=True, production=False)
        return sorted(versions)
