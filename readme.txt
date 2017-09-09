threading:
https://docs.python.org/3/library/threading.html

pygobject reference doc:
https://lazka.github.io/pgi-docs/

debugging:
export GST_DEBUG_DUMP_DOT_DIR=/home/Test/player/dot

todo:
- playbin does not expose a "window-handle" property, while d3dvideosink does

good references:
  https://gstconf.ubicast.tv/videos/gstplayer-a-simple-cross-platform-api-for-all-your-media-playback-needs-part-1/
  http://brettviren.github.io/pygst-tutorial-org/pygst-tutorial.pdf
  http://pyqt.sourceforge.net/Docs/PyQt5/signals_slots.html

*** unpause video hickup ***
investigate clocks and why GstElement calls pipeline_update_start_time twice when PAUSING
calling twice get_clock() can cause a problem as the 2 calls to pipeline_update_start_time won't use same time
stop clock ?
if clock is from audiosink (?) then video might be catching up with audio (audio beeing a bit ahead)
also check if start time is progagated to children as the doc says...

related links:
  https://forums.openpli.org/topic/38783-gstreamer10-pauseunpause-improvements/
  https://bugzilla.gnome.org/show_bug.cgi?id=607842

A #GstPipeline maintains a running time for the elements. The running
time is defined as the difference between the current clock time and
the base time. When the pipeline goes to READY or a flushing seek is
performed on it, the running time is reset to 0. When the pipeline is
set from PLAYING to PAUSED, the current clock time is sampled and used to
configure the base time for the elements when the pipeline is set
to PLAYING again. The effect is that the running time (as the difference
between the clock time and the base time) will count how much time was spent
in the PLAYING state. This default behaviour can be changed with the
gst_element_set_start_time() method.

export GST_DEBUG=d3dvideosink:3
0:01:01.432595607  4044    93a2a88 ERROR           d3dvideosink d3dhelpers.c:1833:d3d_stretch_and_copy:<d3dvideosink0> Failure calling Direct3DDevice9_StretchRect

cannot use playbin3 because of new message type
OverflowError: Python int too large to convert to C long
print(Gst.MessageType.DEVICE_ADDED) ==> <flags GST_MESSAGE_EOS | GST_MESSAGE_EXTENDED | GST_MESSAGE_DEVICE_ADDED of type Gst.MessageType>

https://bugzilla.gnome.org/show_bug.cgi?id=732633
https://cgit.freedesktop.org/gstreamer/gstreamer/commit/gst/gstmessage.h?id=125ae3acb1bc0f45e1e4763ae9ac214013a66765

Exisiting issues:
- why go to pause when seeking ?
- if player is too slow (one seeek takes longer than grace period of 250ms) then




directound:

bug:
0:00:00.520121762  4588    2f129a0 DEBUG        directsoundsink gstdirectsoundsink.c:994:gst_directsound_sink_set_volume:<directsoundsink0> Setting volume on secondary buffer to 0 from 10000
0:00:00.520168617  4588    2f129a0 DEBUG        directsoundsink gstdirectsoundsink.c:994:gst_directsound_sink_set_volume:<directsoundsink0> Setting volume on secondary buffer to 0 from 100








  gstringbuffer_class->open_device
  gstringbuffer_class->close_device
  gstringbuffer_class->acquire
  gstringbuffer_class->release
  gstringbuffer_class->start -> 
  gstringbuffer_class->pause -> reset
  gstringbuffer_class->resume -> start
  gstringbuffer_class->stop -> resets

  gstringbuffer_class->delay
  gstringbuffer_class->activate
  
  
  
  /* we call this function without holding the lock on sink for performance
 * reasons. Try hard to not deal with and invalid ringbuffer and rate. */
static GstClockTime
gst_audio_base_sink_get_time (GstClock * clock, GstAudioBaseSink * sink)
{
  guint64 raw, samples;
  guint delay;
  GstClockTime result;
  GstAudioRingBuffer *ringbuffer;
  gint rate;

  if ((ringbuffer = sink->ringbuffer) == NULL)
    return GST_CLOCK_TIME_NONE;

  if ((rate = ringbuffer->spec.info.rate) == 0)
    return GST_CLOCK_TIME_NONE;

  /* our processed samples are always increasing */
  raw = samples = gst_audio_ring_buffer_samples_done (ringbuffer);

  /* the number of samples not yet processed, this is still queued in the
   * device (not played for playback). */
  delay = gst_audio_ring_buffer_delay (ringbuffer);

  if (G_LIKELY (samples >= delay))
    samples -= delay;
  else
    samples = 0;

  result = gst_util_uint64_scale_int (samples, GST_SECOND, rate);

  GST_DEBUG_OBJECT (sink,
      "processed samples: raw %" G_GUINT64_FORMAT ", delay %u, real %"
      G_GUINT64_FORMAT ", time %" GST_TIME_FORMAT,
      raw, delay, samples, GST_TIME_ARGS (result));

  return result;
}




This is a log extract of a video playback going from playing to pause and back to playing.
The shows the out of audiobasesink gstaudiobasesink.c:545:gst_audio_base_sink_get_time:<directsoundsink0>
The time displayed on the far right is the time that will feed the audio clock that drives the pipeline.

0:00:02.432044715   processed samples: raw 30240, delay 9600, real 20640, time 0:00:00.430000000
0:00:02.437322934   processed samples: raw 30720, delay 9600, real 21120, time 0:00:00.440000000
Pausing...
0:00:02.437831207   processed samples: raw 30720, delay 9600, real 21120, time 0:00:00.440000000
0:00:02.438205430   processed samples: raw 30720, delay 9600, real 21120, time 0:00:00.440000000
0:00:02.438512938   processed samples: raw 31200, delay 0, real 31200, time 0:00:00.650000000 (1)
Playing...
0:00:05.773609712   processed samples: raw 31200, delay 0, real 31200, time 0:00:00.650000000
0:00:05.774203007   processed samples: raw 33600, delay 2880, real 30720, time 0:00:00.640000000 (2)
0:00:05.774348849   processed samples: raw 40800, delay 9600, real 31200, time 0:00:00.650000000

You can see at (1) that the clock jumps forward by more than 200ms.
At (2) it goes back in time by 10ms.

The audio clock time is computed by GstAudioBaseSink based on where it thinks it is (number of samples sent to the device)
minus the delay (number of samples not yet played by the device). The delay is provided by the directsoundsink.

The problem is that when pausing, directsoundsink resets the DirectSoundBuffer and the delay becomes zero.
This explains the jump forward of the clock. Fixing this is not too complex: don't reset the DirectSound buffer and pause it isntead.
But that  but requires that directsoundsink can distinguish a pause from a stop.
Currently pause and stop are not exposed to audiosink children and are handled by the same reset vmethod.
Issue XYZ addresses this.



There is another problem in the audioringbuffer_thread_func where samples are writen to the device.
If the write thread is interrupted by a pause, the sample taht was beeing written is accounted as written although it was not.



02.432044715 : raw 30240, delay 9600, real 20640, time 00.430000000
02.437322934 : raw 30720, delay 9600, real 21120, time 00.440000000
Pausing...
02.437831207 : raw 30720, delay 9600, real 21120, time 00.440000000
02.438205430 : raw 30720, delay 9600, real 21120, time 00.440000000
02.438512938 : raw 31200, delay 0, real 31200, time 00.650000000 (1)
Playing...
05.773609712 : raw 31200, delay 0, real 31200, time 00.650000000
05.774203007 : raw 33600, delay 2880, real 30720, time 00.640000000 (2)
05.774348849 : raw 40800, delay 9600, real 31200, time 00.650000000



Pause is not supposed to flush unless asked to and there is no simple/reliable way to know in audio sinks if the pause should flush.

It makes more sense to call stop to do the flush as it is acceptable if not expected to flush audio sink devices on stop. Flushing devices on pause creates noticeable problems (in directsoundsink for instance...).

The proposed patch simply changes set_flushing to call the stop vmethod instead of pause.
This change does not impact audio sinks that do the same on pause and stop (i.e. do a reset).
But it could impact audio sinks that handle pause and stop distinctivly.

I scanned all audio sinks in the the gstreamer repositories.


GstAudioSink
gst-plugins-good/sys/oss/gstosssink
gst-plugins-good/sys/oss4/oss4-sink
gst-plugins-good/sys/sunaudio/gstsunaudiosink
gst-plugins-good/sys/waveform/gstwaveformsink
gst-plugins-bad/ext/openal/gstopenalsink
gst-plugins-bad/sys/tinyalsa/tinyalsasink
gst-plugins-bad/sys/wasapi/gstwasapisink

GstAudioBaseSink
gst-plugins-base/ext/alsa/gstalsasink
gst-plugins-good/ext/jack/gstjackaudiosink
gst-plugins-good/sys/directsound/gstdirectsoundsink
gst-plugins-bad/sys/opensles/openslessink

Problematic audio sinks:
gst-plugins-good/ext/pulse/pulsesink
gst-plugins-good/sys/osxaudio/gstosxaudiosink



