import os
import getpass
import copy

import attr
from openpype.lib import (
    TextDef,
    BoolDef,
    NumberDef,
)
from openpype.pipeline import (
    legacy_io,
    OpenPypePyblishPluginMixin
)
from openpype.settings import get_project_settings
from openpype.hosts.max.api.lib import (
    get_current_renderer,
    get_multipass_setting
)
from openpype.hosts.max.api.lib_rendersettings import RenderSettings
from openpype_modules.deadline import abstract_submit_deadline
from openpype_modules.deadline.abstract_submit_deadline import DeadlineJobInfo


@attr.s
class MaxPluginInfo(object):
    SceneFile = attr.ib(default=None)   # Input
    Version = attr.ib(default=None)  # Mandatory for Deadline
    SaveFile = attr.ib(default=True)
    IgnoreInputs = attr.ib(default=True)


class MaxSubmitDeadline(abstract_submit_deadline.AbstractSubmitDeadline,
                        OpenPypePyblishPluginMixin):

    label = "Submit Render to Deadline"
    hosts = ["max"]
    families = ["maxrender"]
    targets = ["local"]

    use_published = True
    priority = 50
    chunk_size = 1
    jobInfo = {}
    pluginInfo = {}
    group = None

    @classmethod
    def apply_settings(cls, project_settings, system_settings):
        settings = project_settings["deadline"]["publish"]["MaxSubmitDeadline"]  # noqa

        # Take some defaults from settings
        cls.use_published = settings.get("use_published",
                                         cls.use_published)
        cls.priority = settings.get("priority",
                                    cls.priority)
        cls.chuck_size = settings.get("chunk_size", cls.chunk_size)
        cls.group = settings.get("group", cls.group)

    def get_job_info(self):
        job_info = DeadlineJobInfo(Plugin="3dsmax")

        # todo: test whether this works for existing production cases
        #       where custom jobInfo was stored in the project settings
        job_info.update(self.jobInfo)

        instance = self._instance
        context = instance.context
        # Always use the original work file name for the Job name even when
        # rendering is done from the published Work File. The original work
        # file name is clearer because it can also have subversion strings,
        # etc. which are stripped for the published file.

        src_filepath = context.data["currentFile"]
        src_filename = os.path.basename(src_filepath)

        job_info.Name = "%s - %s" % (src_filename, instance.name)
        job_info.BatchName = src_filename
        job_info.Plugin = instance.data["plugin"]
        job_info.UserName = context.data.get("deadlineUser", getpass.getuser())
        job_info.EnableAutoTimeout = True
        # Deadline requires integers in frame range
        frames = "{start}-{end}".format(
            start=int(instance.data["frameStart"]),
            end=int(instance.data["frameEnd"])
        )
        job_info.Frames = frames

        job_info.Pool = instance.data.get("primaryPool")
        job_info.SecondaryPool = instance.data.get("secondaryPool")

        attr_values = self.get_attr_values_from_data(instance.data)

        job_info.ChunkSize = attr_values.get("chunkSize", 1)
        job_info.Comment = context.data.get("comment")
        job_info.Priority = attr_values.get("priority", self.priority)
        job_info.Group = attr_values.get("group", self.group)

        # Add options from RenderGlobals
        render_globals = instance.data.get("renderGlobals", {})
        job_info.update(render_globals)

        keys = [
            "FTRACK_API_KEY",
            "FTRACK_API_USER",
            "FTRACK_SERVER",
            "OPENPYPE_SG_USER",
            "AVALON_PROJECT",
            "AVALON_ASSET",
            "AVALON_TASK",
            "AVALON_APP_NAME",
            "OPENPYPE_DEV",
            "OPENPYPE_VERSION",
            "IS_TEST"
        ]
        # Add mongo url if it's enabled
        if self._instance.context.data.get("deadlinePassMongoUrl"):
            keys.append("OPENPYPE_MONGO")

        environment = dict({key: os.environ[key] for key in keys
                            if key in os.environ}, **legacy_io.Session)

        for key in keys:
            value = environment.get(key)
            if not value:
                continue
            job_info.EnvironmentKeyValue[key] = value

        # to recognize job from PYPE for turning Event On/Off
        job_info.EnvironmentKeyValue["OPENPYPE_RENDER_JOB"] = "1"
        job_info.EnvironmentKeyValue["OPENPYPE_LOG_NO_COLORS"] = "1"

        # Add list of expected files to job
        # ---------------------------------
        exp = instance.data.get("expectedFiles")
        for filepath in self._iter_expected_files(exp):
            job_info.OutputDirectory += os.path.dirname(filepath)
            job_info.OutputFilename += os.path.basename(filepath)

        return job_info

    def get_plugin_info(self):
        instance = self._instance

        plugin_info = MaxPluginInfo(
            SceneFile=self.scene_path,
            Version=instance.data["maxversion"],
            SaveFile=True,
            IgnoreInputs=True
        )

        plugin_payload = attr.asdict(plugin_info)

        # Patching with pluginInfo from settings
        for key, value in self.pluginInfo.items():
            plugin_payload[key] = value

        return plugin_payload

    def process_submission(self):

        instance = self._instance
        filepath = self.scene_path

        files = instance.data["expectedFiles"]
        if not files:
            raise RuntimeError("No Render Elements found!")
        first_file = next(self._iter_expected_files(files))
        output_dir = os.path.dirname(first_file)
        instance.data["outputDir"] = output_dir
        instance.data["toBeRenderedOn"] = "deadline"

        filename = os.path.basename(filepath)

        payload_data = {
            "filename": filename,
            "dirname": output_dir
        }

        self.log.debug("Submitting 3dsMax render..")
        payload = self._use_published_name(payload_data)
        job_info, plugin_info = payload
        self.submit(self.assemble_payload(job_info, plugin_info))

    def _use_published_name(self, data):
        instance = self._instance
        job_info = copy.deepcopy(self.job_info)
        plugin_info = copy.deepcopy(self.plugin_info)
        plugin_data = {}
        project_setting = get_project_settings(
            legacy_io.Session["AVALON_PROJECT"]
        )

        multipass = get_multipass_setting(project_setting)
        if multipass:
            plugin_data["DisableMultipass"] = 0
        else:
            plugin_data["DisableMultipass"] = 1

        files = instance.data.get("files")
        if not files:
            raise RuntimeError("No render elements found")
        old_output_dir = os.path.dirname(files[0])
        output_beauty = RenderSettings().get_render_output(instance.name,
                                                           old_output_dir)
        files = instance.data["expectedFiles"]
        first_file = next(self._iter_expected_files(files))
        rgb_bname = os.path.basename(output_beauty)
        dir = os.path.dirname(first_file)
        beauty_name =  f"{dir}/{rgb_bname}"
        beauty_name = beauty_name.replace("\\", "/")
        plugin_data["RenderOutput"] = beauty_name
        # as 3dsmax has version with different languages
        plugin_data["Language"] = "ENU"
        renderer_class = get_current_renderer()

        renderer = str(renderer_class).split(":")[0]
        if renderer in [
            "ART_Renderer",
            "Redshift_Renderer",
            "V_Ray_6_Hotfix_3",
            "V_Ray_GPU_6_Hotfix_3",
            "Default_Scanline_Renderer",
            "Quicksilver_Hardware_Renderer",
        ]:
            render_elem_list = RenderSettings().get_render_element()
            for i, element in enumerate(render_elem_list):
                elem_bname = os.path.basename(element)
                new_elem = f"{dir}/{elem_bname}"
                new_elem = new_elem.replace("/", "\\")
                plugin_data["RenderElementOutputFilename%d" % i] = new_elem   # noqa


        self.log.debug("plugin data:{}".format(plugin_data))
        plugin_info.update(plugin_data)

        return job_info, plugin_info

    def from_published_scene(self, replace_in_path=True):
        instance = self._instance
        workfile_instance = self._get_workfile_instance(instance.context)
        if workfile_instance is None:
            return

        # determine published path from Anatomy.
        template_data = workfile_instance.data.get("anatomyData")
        rep = workfile_instance.data["representations"][0]
        template_data["representation"] = rep.get("name")
        template_data["ext"] = rep.get("ext")
        template_data["comment"] = None

        anatomy = instance.context.data['anatomy']
        template_obj = anatomy.templates_obj["publish"]["path"]
        template_filled = template_obj.format_strict(template_data)
        file_path = os.path.normpath(template_filled)

        self.log.info("Using published scene for render {}".format(file_path))

        if not os.path.exists(file_path):
            self.log.error("published scene does not exist!")
            raise

        if not replace_in_path:
            return file_path

        # now we need to switch scene in expected files
        # because <scene> token will now point to published
        # scene file and that might differ from current one
        def _clean_name(path):
            return os.path.splitext(os.path.basename(path))[0]

        new_scene = _clean_name(file_path)
        orig_scene = _clean_name(instance.data["AssetName"])
        expected_files = instance.data.get("expectedFiles")

        if isinstance(expected_files[0], dict):
            # we have aovs and we need to iterate over them
            new_exp = {}
            for aov, files in expected_files[0].items():
                replaced_files = []
                for f in files:
                    replaced_files.append(
                        str(f).replace(orig_scene, new_scene)
                    )
                new_exp[aov] = replaced_files
            # [] might be too much here, TODO
            instance.data["expectedFiles"] = [new_exp]
        else:
            new_exp = []
            for f in expected_files:
                new_exp.append(
                    str(f).replace(orig_scene, new_scene)
                )
            instance.data["expectedFiles"] = new_exp

        metadata_folder = instance.data.get("publishRenderMetadataFolder")
        if metadata_folder:
            metadata_folder = metadata_folder.replace(orig_scene,
                                                      new_scene)
            instance.data["publishRenderMetadataFolder"] = metadata_folder

        self.log.info("Scene name was switched {} -> {}".format(
            orig_scene, new_scene
        ))

        return file_path

    @staticmethod
    def _iter_expected_files(exp):
        if isinstance(exp[0], dict):
            for _aov, files in exp[0].items():
                for file in files:
                    yield file
        else:
            for file in exp:
                yield file

    @classmethod
    def get_attribute_defs(cls):
        defs = super(MaxSubmitDeadline, cls).get_attribute_defs()
        defs.extend([
            BoolDef("use_published",
                    default=cls.use_published,
                    label="Use Published Scene"),

            NumberDef("priority",
                      minimum=1,
                      maximum=250,
                      decimals=0,
                      default=cls.priority,
                      label="Priority"),

            NumberDef("chunkSize",
                      minimum=1,
                      maximum=50,
                      decimals=0,
                      default=cls.chunk_size,
                      label="Frame Per Task"),

            TextDef("group",
                    default=cls.group,
                    label="Group Name"),
        ])

        return defs
