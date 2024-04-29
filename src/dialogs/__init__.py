from .boxes import consensus_box, error_box, info_box, warn_box
from .detect import PicterraDialogDetect
from .entities import PicterraDialogEntities
from .operations import PicterraDialogOperations
from .settings import PicterraDialogSettings
from .upload import PicterraDialogUpload

ADMIN_PREFIX = (
    "13489238479217283"  # we have this prefix for security/obfuscation purposes
)

__all__ = [
    consensus_box,
    error_box,
    info_box,
    warn_box,
    PicterraDialogDetect,
    PicterraDialogEntities,
    PicterraDialogOperations,
    PicterraDialogSettings,
    PicterraDialogUpload,
]
