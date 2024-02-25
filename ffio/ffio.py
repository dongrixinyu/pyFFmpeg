import os
import time
import cv2
import base64
import numpy as np

from PIL    import Image
from ctypes import Structure, PyDLL, POINTER, c_int, c_bool, c_char_p, py_object, c_char, byref

DIR_PATH = os.path.dirname(os.path.abspath(__file__))


class CFFIO(Structure):
  _fields_ = [
    ("ffio_state",         c_int),
    ("ffio_mode",          c_int),
    ("frame_seq",          c_int),
    ("hw_enabled",         c_bool),
    ("shm_enabled",        c_bool),
    ("shm_fd",             c_int),
    ("shm_size",           c_int),
    ("video_stream_index", c_int),
    ("image_width",        c_int),
    ("image_height",       c_int),
    ("image_byte_size",    c_int)
  ]


class CCodecParams(Structure):
  width      : int
  height     : int
  bitrate    : int
  fps        : int
  gop        : int
  b_frames   : int
  profile    : str
  preset     : str
  tune       : str
  pix_fmt    : str
  format     : str

  _fields_ = [
    ("width",    c_int),
    ("height",   c_int),
    ("bitrate",  c_int),
    ("fps",      c_int),
    ("gop",      c_int),
    ("b_frames", c_int),
    ("profile",  c_char * 24),
    ("preset",   c_char * 24),
    ("tune",     c_char * 24),
    ("pix_fmt",  c_char * 24),
    ("format",   c_char * 24)
  ]


c_lib = PyDLL(os.path.join(DIR_PATH, 'build', 'libinterfaceAPI.so'))

c_lib.api_newFFIO.argtypes = []
c_lib.api_newFFIO.restype  = POINTER(CFFIO)

c_lib.api_initFFIO.argtypes = [
  POINTER(CFFIO), c_int, c_char_p,
  c_bool, c_char_p,
  c_bool, c_char_p, c_int, c_int,
  POINTER(CCodecParams)
]
c_lib.api_initFFIO.restype  = None

c_lib.api_finalizeFFIO.argtypes = [POINTER(CFFIO)]
c_lib.api_finalizeFFIO.restype  = None
c_lib.api_deleteFFIO.argtypes   = [POINTER(CFFIO)]
c_lib.api_deleteFFIO.restype    = None

c_lib.api_decodeOneFrame.argtypes      = [POINTER(CFFIO)]
c_lib.api_decodeOneFrame.restype       = py_object
c_lib.api_decodeOneFrameToShm.argtypes = [POINTER(CFFIO), c_int]
c_lib.api_decodeOneFrameToShm.restype  = c_bool

c_lib.api_encodeOneFrame.argtypes        = [POINTER(CFFIO), py_object]
c_lib.api_encodeOneFrame.restype         = c_int
c_lib.api_encodeOneFrameFromShm.argtypes = [POINTER(CFFIO), c_int]
c_lib.api_encodeOneFrameFromShm.restype  = c_bool


class FFIO(object):
  _c_ffio_ptr  : POINTER(CFFIO)
  target_url   : str
  mode         : int      # mode == 0 ? decode : encode
  frame_seq_py : int      # from this py class:FFIO
  frame_seq_c  : int      # from c struct:FFIO
  shm_enabled  : bool
  width        : int
  height       : int
  ffio_state   : int
  codec_params : CCodecParams

  def __init__(self, target_url: str, mode: int = 0, hw_enabled: bool = False,
               shm_name: str = None, shm_size: int = 0, shm_offset: int = 0,
               codec_params: CCodecParams = None):
    start_time = time.time()

    self.target_url   = target_url
    self.mode         = mode
    self.frame_seq_py = 0
    self.codec_params = codec_params if codec_params is not None else CCodecParams()
    self._c_ffio_ptr  = c_lib.api_newFFIO()

    if shm_name is None:
      self.shm_enabled = False
      c_lib.api_initFFIO(
        self._c_ffio_ptr, mode, self.target_url.encode(),
        hw_enabled, "cuda".encode(),
        self.shm_enabled, "".encode(), 0, 0,
        byref(self.codec_params)
      )
    else:
      self.shm_enabled = True
      c_lib.api_initFFIO(
        self._c_ffio_ptr, mode, self.target_url.encode(),
        hw_enabled, "cuda".encode(),
        self.shm_enabled, shm_name.encode(), shm_size, shm_offset,
        byref(self.codec_params)
      )

    end_time = time.time()

    if self._c_ffio_ptr.contents.ffio_state == 1:  # succeeded
      print(f"inited ffio after: {(end_time-start_time):.4f} seconds.")
      print(f"open stream with: {self._c_ffio_ptr.contents.image_width}x{self._c_ffio_ptr.contents.image_height}.")

      self.width  = self._c_ffio_ptr.contents.image_width
      self.height = self._c_ffio_ptr.contents.image_height
    else:
      print(f"failed to initialize ffio after: {(end_time-start_time):.4f} seconds.")
      c_lib.api_deleteFFIO(self._c_ffio_ptr)

  @property
  def ffio_state(self) -> bool:
    state = self._c_ffio_ptr.contents.ffio_state
    return True if state == 1 or state == 2 else False

  @property
  def frame_seq_c(self):
    return self._c_ffio_ptr.contents.frame_seq

  def decode_one_frame(self, image_format: str = "numpy"):
    # image_format: numpy, Image, base64, None
    ret = c_lib.api_decodeOneFrame(self._c_ffio_ptr)
    ret_type = type(ret)
    if ret_type is bytes:  # rgb data of image will return with type: bytes.
      self.frame_seq_py += 1
      if image_format is None:
        return ret
      elif image_format == 'numpy':
        np_buffer = np.frombuffer(ret, dtype=np.uint8)
        np_frame  = np.reshape(np_buffer, (self.height, self.width, 3))
        return np_frame
      elif image_format == 'Image':
        rgb_image = Image.frombytes("RGB", (self.width, self.height), ret)
        return rgb_image
      elif image_format == 'base64':
        np_buffer = np.frombuffer(ret, dtype=np.uint8)
        np_frame  = np.reshape(np_buffer, (self.height, self.width, 3))
        bgr_image = cv2.cvtColor(np_frame, cv2.COLOR_RGB2BGR)
        np_image  = cv2.imencode('.jpg', bgr_image)[1]
        base64_image_code = base64.b64encode(np_image).decode()
        return base64_image_code

    elif ret_type is int:
      if ret == -5:
        # it means that the stream is empty,
        # now you should close the stream context object manually
        return -5
      elif ret == -4:
        # Packet mismatch. Unable to seek to the next packet
        # now you should close the stream context object manually
        return -4
      else:
        # other errors
        return 1

  def decode_one_frame_to_shm(self, offset=0) -> bool:
    # get RGB bytes to shm.
    return c_lib.api_decodeOneFrameToShm(self._c_ffio_ptr, offset)

  def encode_one_frame(self, rgb_image) -> bool:
    rgb_image_type = type(rgb_image)
    if rgb_image_type is bytes:
      ret = c_lib.api_encodeOneFrame(self._c_ffio_ptr, rgb_image)
      if ret == 0:
        self.frame_seq_py += 1
        return True
      return False
    elif rgb_image_type == np.ndarray:
      rgb_image_bytes = rgb_image.tobytes()
      return self.encode_one_frame(rgb_image_bytes)
    elif rgb_image_type == Image:
      return False
    return False

  def encode_one_frame_from_shm(self, offset=0) -> bool:
    return c_lib.api_encodeOneFrameFromShm(self._c_ffio_ptr, offset)

  def release_memory(self):
    c_lib.api_finalizeFFIO(self._c_ffio_ptr)
    c_lib.api_deleteFFIO(self._c_ffio_ptr)

    self.width        = 0
    self.height       = 0
    self.frame_seq_py = 0