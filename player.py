import sys
import os
import threading
import time
import re
from enum import Enum

import gi
gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import GObject, GLib
from gi.repository import Gst, GstVideo

from PyQt5.QtCore import QObject, pyqtSignal

from media_info import PlayerMediaInfo

class PlayerState(Enum):
    STOPPED = 1
    BUFFERING = 2
    PAUSED = 3
    PLAYING = 4

class Player(QObject):

    GRACE_PERIOD = 250

    # signals
    state_changed = pyqtSignal('PyQt_PyObject')
    duration_changed = pyqtSignal('PyQt_PyObject')
    position_updated = pyqtSignal('PyQt_PyObject')

    #@GObject.Property(type=int)
    #def prop_gint(self):
    #    return self.value

    #@prop_gint.setter
    #def prop_gint(self, value):
    #    print("XXX")
    #    print(value)
    #    self.value = value

    def __init__(self, args):
        super().__init__()

        # configuration
        self.mute = args.mute
        self.fast_seek = args.fast_seek
        self.accurate = not self.fast_seek

        # player
        self.uri = None

        self.thread = None
        self.lock = GLib.Mutex()
        self.cond = GLib.Cond()
        self.context = None
        self.loop = None

        self.playbin = None
        self.bus = None
        self.target_state = None
        self.current_state = None
        self.is_live = None
        self.is_eos = None
        self.tick_source = None
        self.ready_timeout_source = None
        self.cached_duration = None

        self.rate = None

        self.app_state = None
        self.buffering = None

        self.media_info = None

        # protected by lock
        self.seek_pending = None        # Only set from main contect
        self.last_seek_time = None      # Only set from main contect
        self.seek_source = None
        self.seek_position = None
        # If TRUE, all signals are inhibited except the
        # state-changed:GST_PLAYER_STATE_STOPPED/PAUSED. This ensures that no signal
        # is emitted after gst_player_stop/pause() has been called by the user. */
        self.inhibit_sigs = None

        # For playbin3
        self.use_playbin3 = None

        self.init()
        self.constructed()

    def __del__(self):
        print("*** __del__")
        #self.dispose()
        self.finalize()
        s = super()
        try:
            s.__del__
        except AttributeError:
            pass
        else:
            s.__del__(self)

    def init(self):
        self.lock.init()
        self.cond.init()

        self.context = GLib.MainContext()
        self.loop = GLib.MainLoop(self.context)

        self.seek_pending = False
        self.seek_position = Gst.CLOCK_TIME_NONE
        self.last_seek_time = -1
        self.inhibit_sigs = False

    def dispose(self):
        print("Stopping main thread")
        if self.loop:
            self.loop.quit()

            self.thread.join()
            self.thread = None

            #self.loop.unref()
            self.loop = None

            #self.context.unref()
            self.context = None

    def finalize(self):
        print("Finalizing")
        #if (self.global_tags)
        #gst_tag_list_unref (self.global_tags)
        #if (self.video_renderer)
        #g_object_unref (self.video_renderer)
        #if (self.signal_dispatcher)
        #g_object_unref (self.signal_dispatcher)
        #if (self.current_vis_element)
        #gst_object_unref (self.current_vis_element)
        #if (self.config)
        #gst_structure_free (self.config)
        #if (self.collection)
        #gst_object_unref (self.collection)
        self.lock.clear()
        self.cond.clear()

    def constructed(self):
        print("Constructed")
        self.lock.lock()
        self.thread = threading.Thread(name="GstPlayer", target=self.main)
        self.thread.start()
        while not self.loop or not self.loop.is_running():
            self.cond.wait(self.lock)
        self.lock.unlock()

    def set_uri_internal(self):

        self.stop_internal(False)

        self.lock.lock()

        print("Changing URI to '%s'" % self.uri)

        #TODO g_object_set (self.playbin, "uri", self.uri, NULL)
        #GObject.set(self.playbin, "uri", self.uri, None)
        self.playbin.set_property("uri", self.uri)

        #if (g_signal_handler_find (self, G_SIGNAL_MATCH_ID,
        #  signals[SIGNAL_URI_LOADED], 0, NULL, NULL, NULL) != 0) {
        #UriLoadedSignalData *data = g_new (UriLoadedSignalData, 1)

        #data.player = g_object_ref (self)
        #data.uri = g_strdup (self.uri)
        #gst_player_signal_dispatcher_dispatch (self.signal_dispatcher, self,        uri_loaded_dispatch, data,        (GDestroyNotify) uri_loaded_signal_data_free)

        # TODO self.playbin.set("suburi", None, None)

        self.lock.unlock()

        return GLib.SOURCE_REMOVE

    def set_rate_internal(self):
        self.seek_position = self.get_position()
        # TODO fix comment
        # If there is no seek being dispatch to the main context currently do that,
        # otherwise we just updated the rate so that it will be taken by
        # the seek handler from the main context instead of the old one.
        if not self.seek_source:
            # If no seek is pending then create new seek source
            if not self.seek_pending:
                self.seek_source = GLib.idle_source_new()
                self.seek_source.set_callback(self.seek_internal)
                self.seek_source.attach(self.context)
    #
    # set_property()
    #

    def set_uri(self, value):
      self.lock.lock()
      #g_free (self.uri)
      #g_free (self.redirect_uri)
      #self.redirect_uri = NULL

      #g_free (self.suburi)
      self.suburi = None

      self.uri = value
      print("Set uri=%s" % self.uri)
      self.lock.unlock()

      self.context.invoke_full(GLib.PRIORITY_DEFAULT, self.set_uri_internal)

    def set_rate(self, rate):
        self.lock.lock()
        self.rate = rate
        print("Set rate=%s" % rate)
        self.set_rate_internal()
        self.lock.unlock()

    def set_position(self, position):
        self.seek(position)

    def get_position(self):
        (success, position) = Gst.Element.query_position(self.playbin, Gst.Format.TIME)
        return position

    #
    # get_property()
    #

    def get_duration(self):
        return self.cached_duration

    def main_loop_running_cb(self, data):
        print("Main loop running now")

        self.lock.lock()
        self.cond.signal()
        self.lock.unlock()

        return GLib.SOURCE_REMOVE

    def state_changed_dispatch(self, state):
        if self.inhibit_sigs and state != PlayerState.STOPPED and state != PlayerState.PAUSED:
            return
        self.state_changed.emit(state)

    def change_state(self, state):
        if state == self.app_state:
            return

        print("Changing app state from %s to %s" % (self.app_state, state))
        self.app_state = state

        self.state_changed_dispatch(state)

    def position_updated_dispatch(self, position):
        if self.inhibit_sigs:
            return
        if self.target_state >= Gst.State.PAUSED:
            self.position_updated.emit(position)

    def tick_cb(self, data=None):
        if self.target_state >= Gst.State.PAUSED:
            (success, position) = Gst.Element.query_position(self.playbin, Gst.Format.TIME)
            if success:
                print("Position %s" % position)
                self.position_updated_dispatch(position)
        return GLib.SOURCE_CONTINUE

    def add_tick_source(self):
        if self.tick_source:
            return

        position_update_interval_ms = 50
        #gst_player_config_get_position_update_interval (self.config)
        if not position_update_interval_ms:
            return

        self.tick_source = GLib.timeout_source_new(position_update_interval_ms)
        self.tick_source.set_callback(self.tick_cb)
        self.tick_source.attach(self.context)

    def remove_tick_source(self):
        if not self.tick_source:
            return
        self.tick_source.destroy()
        #self.tick_source.unref()
        self.tick_source = None

    def ready_timeout_cb(self, user_data):
        if self.target_state <= Gst.State.READY:
            print("Setting pipeline to NULL state")
            self.target_state = Gst.State.NULL
            self.current_state = Gst.State.NULL
            self.playbin.set_state(Gst.State.NULL)
        return GLib.SOURCE_REMOVE

    def add_ready_timeout_source(self):
        if self.ready_timeout_source:
            return

        self.ready_timeout_source = GLib.timeout_source_new_seconds(60)
        self.ready_timeout_source.set_callback(self.ready_timeout_cb, None)
        self.ready_timeout_source.attach(self.context)

    def remove_ready_timeout_source(self):
        if not self.ready_timeout_source:
            return
        self.ready_timeout_source.destroy()
        #self.ready_timeout_source.unref()
        self.ready_timeout_source = None

    def error_dispatch(self, user_data):
        if self.inhibit_sigs:
            return
        #g_signal_emit (data.player, signals[SIGNAL_ERROR], 0, data.err)

    def emit_error(self, err):
        #       g_quark_to_string (err.domain)
        print("Error: %s (%s, %s)" % (err.message, "", err.code))

        #if (g_signal_handler_find (self, G_SIGNAL_MATCH_ID,
        #  signals[SIGNAL_ERROR], 0, NULL, NULL, NULL) != 0) {
        #ErrorSignalData *data = g_new (ErrorSignalData, 1)
        #data.player = g_object_ref (self)
        #data.err = g_error_copy (err)
        #gst_player_signal_dispatcher_dispatch (self.signal_dispatcher, self,
        #error_dispatch, data, (GDestroyNotify) free_error_signal_data)
        self.error_dispatch(err)

        self.remove_tick_source()
        self.remove_ready_timeout_source()

        self.target_state = Gst.State.NULL
        self.current_state = Gst.State.NULL
        self.is_live = False
        self.is_eos = False
        self.playbin.set_state(Gst.State.NULL)
        self.change_state(PlayerState.STOPPED)
        self.buffering = 100

        self.lock.lock()
        if self.media_info:
            self.media_info = None

        #if (self.global_tags) {
        #gst_tag_list_unref (self.global_tags)
        #self.global_tags = NULL

        self.seek_pending = False
        self.remove_seek_source()
        self.seek_position = Gst.CLOCK_TIME_NONE
        self.last_seek_time = -1
        self.lock.unlock()

    def warning_dispatch(self, user_data):
        if self.inhibit_sigs:
            return
        #g_signal_emit (data.player, signals[SIGNAL_WARNING], 0, data.err)

    def emit_warning(self, err):
        #       g_quark_to_string (err.domain)
        print("Warning: %s (%s, %s)" % (err.message, "", err.code))

        #if (g_signal_handler_find (self, G_SIGNAL_MATCH_ID,
        #  signals[SIGNAL_ERROR], 0, NULL, NULL, NULL) != 0) {
        #ErrorSignalData *data = g_new (ErrorSignalData, 1)
        #data.player = g_object_ref (self)
        #data.err = g_error_copy (err)
        #gst_player_signal_dispatcher_dispatch (self.signal_dispatcher, self,
        #error_dispatch, data, (GDestroyNotify) free_error_signal_data)
        self.warning_dispatch(err)

    def error_cb(self, bus, msg, user_data=None):
        Gst.debug_bin_to_dot_file(self.playbin, Gst.DebugGraphDetails.ALL, "player_error")
        (err, debug) = msg.parse_error()
        #self.log(msg, err)
        self.log(msg, "")


        #name = gst_object_get_path_string (msg.src)
        #message = gst_error_get_message (err.domain, err.code)

        #if (debug)
        #full_message =        g_strdup_printf ("Error from element %s: %s\n%s\n%s", name, message,        err.message, debug)
        #else
        #full_message =       g_strdup_printf ("Error from element %s: %s\n%s", name, message,        err.message)

        #GST_ERROR_OBJECT (self, "ERROR: from element %s: %s\n", name, err.message)
        #if (debug != NULL)
        #GST_ERROR_OBJECT (self, "Additional debug info:\n%s\n", debug)

        #player_err =      g_error_new_literal (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED,      full_message)
        self.emit_error(err)

        #g_clear_error (&err)

    def warning_cb(self, bus, msg, user_data=None):
        pass
        #dump_dot_file (self, "warning")

        #gst_message_parse_warning (msg, &err, &debug)

        #name = gst_object_get_path_string (msg.src)
        #message = gst_error_get_message (err.domain, err.code)

        #if (debug)
        #full_message =        g_strdup_printf ("Warning from element %s: %s\n%s\n%s", name, message,        err.message, debug)
        #else
        #full_message =        g_strdup_printf ("Warning from element %s: %s\n%s", name, message,        err.message)

        #GST_WARNING_OBJECT (self, "WARNING: from element %s: %s\n", name,      err.message)
        #if (debug != NULL)
        #GST_WARNING_OBJECT (self, "Additional debug info:\n%s\n", debug)

        #player_err =      g_error_new_literal (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED,      full_message)
        #emit_warning (self, player_err)

        #g_clear_error (&err)

    def eos_dispatch(self):
        if self.inhibit_sigs:
            return
        #g_signal_emit (player, signals[SIGNAL_END_OF_STREAM], 0)


    def eos_cb(self, bus, msg, user_data=None):
        print("End of stream")

        self.tick_cb()
        self.remove_tick_source()

        #if (g_signal_handler_find (self, G_SIGNAL_MATCH_ID,
        #  signals[SIGNAL_END_OF_STREAM], 0, NULL, NULL, NULL) != 0) {
        #gst_player_signal_dispatcher_dispatch (self.signal_dispatcher, self,
        #eos_dispatch, g_object_ref (self), (GDestroyNotify) g_object_unref)
        self.eos_dispatch()

        self.change_state(PlayerState.STOPPED)
        self.buffering = 100
        self.is_eos = True

    def clock_lost_cb(self, bus, msg, user_data=None):
        print("Clock lost")
        if self.target_state >= Gst.State.PLAYING:
            state_ret = self.playbin.set_state(Gst.State.PAUSED)
            if state_ret != Gst.StateChangeReturn.FAILURE:
                state_ret = self.playbin.set_state(Gst.State.PLAYING)

            if state_ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to handle clock loss")
                #emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED,              "Failed to handle clock loss"));

    def duration_changed_dispatch(self, duration):
        if self.inhibit_sigs:
            return
        if self.target_state >= Gst.State.PAUSED:
            self.duration_changed.emit(duration)

    def emit_duration_changed(self, duration):
        updated = False

        if self.cached_duration == duration:
            return

        print("Duration changed %s" % duration)

        self.cached_duration = duration
        self.lock.lock()
        if self.media_info:
            self.media_info.duration = duration
            updated = True
        self.lock.unlock()
        #if updated:
        #    self.emit_media_info_updated_signal()
        self.duration_changed_dispatch(duration)

    def seek_done_dispatch(self, position):
        if self.inhibit_sigs:
            return

        #g_signal_emit (data->player, signals[SIGNAL_SEEK_DONE], 0, data->position);

    def emit_seek_done(self):
        #if (g_signal_handler_find (self, G_SIGNAL_MATCH_ID,          signals[SIGNAL_SEEK_DONE], 0, NULL, NULL, NULL) != 0) {
        #SeekDoneSignalData *data = g_new (SeekDoneSignalData, 1);
        #data->player = g_object_ref (self);
        #data->position = gst_player_get_position (self);
        #gst_player_signal_dispatcher_dispatch (self->signal_dispatcher, self,
        #seek_done_dispatch, data, (GDestroyNotify) seek_done_signal_data_free);
        self.seek_done_dispatch(self.get_position())

    def state_changed_cb(self, bus, msg, user_data=None):
        if msg.src == self.playbin:

            # TODO no need to parse before test (propose upstream...)
            (old_state, new_state, pending_state) = Gst.Message.parse_state_changed(msg)

            self.log(msg, (str_enum(old_state), str_enum(new_state), str_enum(pending_state)))
            #print("Changed state old: %s new: %s pending: %s" % (old_state, new_state, pending_state))

            self.current_state = new_state

            if old_state == Gst.State.READY and new_state == Gst.State.PAUSED and pending_state == Gst.State.VOID_PENDING:
                print("Initial PAUSED - pre-rolled")

                Gst.debug_bin_to_dot_file(self.playbin, Gst.DebugGraphDetails.ALL, "player_paused")

                self.lock.lock()
                if self.media_info:
                    self.media_info = None
                self.media_info = self.media_info_create()
                self.lock.unlock()
                #self.emit_media_info_updated_signal()

                (success, duration) = self.playbin.query_duration(Gst.Format.TIME)
                if success:
                    self.emit_duration_changed(duration)
                else:
                    self.cached_duration = Gst.CLOCK_TIME_NONE
            if new_state == Gst.State.PAUSED and pending_state == Gst.State.VOID_PENDING:
                # to PAUSED
                self.remove_tick_source()
                self.lock.lock()
                if self.seek_pending:
                    self.seek_pending = False
                    if not self.media_info.seekable:
                        print("Media is not seekable")
                        if self.seek_source:
                            seek_source.destroy()
                            #seek_source.unref()
                            seek_source = None
                        self.seek_position = Gst.CLOCK_TIME_NONE
                        self.last_seek_time = -1
                    elif self.seek_source:
                        print("Seek finished but new seek is pending")
                        self.seek_internal_locked()
                    else:
                        print("Seek finished")
                        self.emit_seek_done()
                else:
                    # HACK
                    env = os.getenv("GST_PLAYER_PAUSE_SEEK_HACK_2")
                    if env and env.startswith("1"):
                        # this breaks stepping...
                        self.seek_position = self.get_position()
                        #self.seek_internal()
                    # HACK

                if self.seek_position != Gst.CLOCK_TIME_NONE:
                    print("Seeking now that we reached PAUSED state")
                    self.seek_internal_locked()
                    self.lock.unlock()
                elif not self.seek_pending:
                    self.lock.unlock()

                    self.tick_cb()

                    if self.target_state >= Gst.State.PLAYING and self.buffering == 100:
                        state_ret = self.playbin.set_state(Gst.State.PLAYING)
                        if state_ret == Gst.StateChangeReturn.FAILURE:
                            #{emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED, "Failed to play"))
                            print("Failed to play")
                    elif self.buffering == 100:
                        self.change_state(PlayerState.PAUSED)
                else:
                    self.lock.unlock()
            elif new_state == Gst.State.PLAYING and pending_state == Gst.State.VOID_PENDING:
                # if no seek is currently pending, add the tick source. This can happen
                # if we seeked already but the state-change message was still queued up
                if not self.seek_pending:
                    self.add_tick_source()
                    self.change_state(PlayerState.PLAYING)
            elif new_state == Gst.State.READY and old_state > Gst.State.READY:
                #self.remove_tick_source()
                self.change_state(PlayerState.STOPPED)
            #else:
                #self.current_state = Gst.State.NULL
                #self.tick_timer.stop()
                #self.seek_pending = False
                #self.seek_position = Gst.CLOCK_TIME_NONE
                #self.change_state(PlayerState.STOPPED)
                #raise ValueError('bad playbin state')

    def duration_changed_cb(self, bus, msg, user_data=None):
        (success, duration) = self.playbin.query_duration(Gst.Format.TIME)
        if success:
            self.emit_duration_changed(duration)

    def latency_cb(self, bus, msg, user_data=None):
        print("Latency changed")
        self.playbin.recalculate_latency()

    def media_info_create(self):
        media_info = PlayerMediaInfo()
        media_info.duration = self.get_duration()
        #media_info.tags = self.global_tags
        media_info.is_live = self.is_live
        #self.global_tags = None

        query = Gst.Query.new_seeking(Gst.Format.TIME)
        if Gst.Element.query(self.playbin, query):
            (format, media_info.seekable, segment_start, segment_end) = Gst.Query.parse_seeking(query)

        return media_info

    def message_cb(self, bus, msg, user_data=None):
        t = msg.type
        if t == Gst.MessageType.STATE_CHANGED:
            pass
        elif t == Gst.MessageType.DURATION_CHANGED:
            self.log(msg, "")
        elif t == Gst.MessageType.LATENCY:
            self.log(msg, "")
        elif t == Gst.MessageType.STEP_START:
            step = msg.parse_step_start()
            #self.log(msg, step)
        elif t == Gst.MessageType.STEP_DONE:
            step = msg.parse_step_done()
            #self.log(msg, step)
        elif t == Gst.MessageType.ASYNC_START:
            # This msg is not forwarded to the application but is used internally.
            self.log(msg, "")
        elif t == Gst.MessageType.ASYNC_DONE:
            running_time = msg.parse_async_done()
            self.log(msg, running_time)
        elif t == Gst.MessageType.CLOCK_PROVIDE:
            (clock, ready) = msg.parse_clock_provide()
            self.log(msg, (clock, ready))
        elif t == Gst.MessageType.CLOCK_LOST:
            clock = msg.parse_clock_lost()
            self.log(msg, clock)
        elif t == Gst.MessageType.NEW_CLOCK:
            clock = msg.parse_new_clock()
            self.log(msg, clock)
        elif t == Gst.MessageType.EOS:
            self.log(msg, "")
        elif t == Gst.MessageType.ERROR:
            pass
        elif t == Gst.MessageType.WARNING:
            pass
        elif t == Gst.MessageType.INFO:
            (err, debug) = msg.parse_info()
            self.log(msg, (err, debug))
            pass
        elif t == Gst.MessageType.QOS:
            qos = msg.parse_qos()
            qos_values = msg.parse_qos_values()
            qos_stats = msg.parse_qos_stats()
            self.log(msg, (qos, qos_values, qos_stats))
            #Gst.debug_bin_to_dot_file(self.playbin, Gst.DebugGraphDetails.ALL, "player_qos")
        elif t == Gst.MessageType.ELEMENT:
            struct = msg.get_structure()
            if struct.get_name() == "GstNavigationMessage":
                pass
            else:
                self.log(msg, str(struct.get_name()))
        elif t == Gst.MessageType.STREAM_STATUS:
            (stream_status_type, owner) = msg.parse_stream_status()
            self.log(msg, ( str_enum(stream_status_type), owner.get_name()))
        elif t == Gst.MessageType.STREAM_COLLECTION:
            stream_collection = msg.parse_stream_collection()
            self.log(msg, stream_collection)
        elif t == Gst.MessageType.TAG:
            tag_list = msg.parse_tag()
            #self.log(msg, tag_list)
        else:
            self.log(msg, "")

    def message_cb_safe(self, bus, msg, user_data=None):
        try:
            self.message_cb(bus, msg, user_data=None)
        except:
            print("%s: %s" % (sys.exc_info()[0].__name__, sys.exc_info()[1]))
            pass
        return

    def on_sync_message(self, bus, msg, user_data=None):
        pass

    def main(self):
        print("Starting main thread")

        self.context.push_thread_default()

        source = GLib.idle_source_new()
        source.set_callback(self.main_loop_running_cb)
        source.attach(self.context)
        #source.unref()
        source = None

        env = os.getenv("GST_PLAYER_USE_PLAYBIN3")
        if env and env.startswith("1"):
            self.use_playbin3 = True

        if self.use_playbin3:
            print("playbin3 enabled")
            self.playbin = Gst.ElementFactory.make("playbin3", "playbin3")
        else:
            self.playbin = Gst.ElementFactory.make("playbin", "playbin")

        # NEW
        if self.mute:
            print("Muting")
            env = os.getenv("GST_PLAYER_FAKE_SINK_MUTE")
            if env and env.startswith("1"):
                audio_sink = Gst.ElementFactory.make("fakesink")
                audio_sink.set_property("sync", True)
                self.playbin.set_property("audio-sink", audio_sink)
            else:
                self.playbin.set_property("mute", True)
        else:
            if 0:
                audio_sink = Gst.ElementFactory.make("waveformsink")
                audio_sink.set_property("sync", True)
                print(audio_sink)
                #audio_sink = Gst.ElementFactory.make("appsink")
                #audio_sink.set_property("sync", True)
                self.playbin.set_property("audio-sink", audio_sink)
            if 0:
                video_sink = Gst.ElementFactory.make("glimagesink")
                #video_sink.set_property("sync", True)
                self.playbin.set_property("video-sink", video_sink)

        # need an event loop ?!
        self.playbin.connect("about-to-finish", self.about_to_finish)
        # NEW

        self.bus = self.playbin.get_bus()
        #bus_source = self.bus.create_watch()
        #bus_source.set_callback(GLib.gst_bus_async_signal_func, NULL, NULL)
        #bus_source.attach(self.context)
        self.bus.add_signal_watch()

        self.bus.connect("message::error", self.error_cb)
        self.bus.connect("message::warning", self.warning_cb)
        self.bus.connect("message::eos", self.eos_cb)
        self.bus.connect("message::state-changed", self.state_changed_cb)
        #self.bus.connect("message::buffering", slef.buffering_cb)
        self.bus.connect("message::clock-lost", self.clock_lost_cb)
        self.bus.connect("message::duration-changed", self.duration_changed_cb)
        self.bus.connect("message::latency", self.latency_cb)
        #self.bus.connect("message::request-state", self.request_state_cb)
        #self.bus.connect("message::element", self.element_cb)
        #self.bus.connect("message::tag", self.tags_cb)

        self.bus.connect("message", self.message_cb_safe)

        if self.use_playbin3:
            #self.bus.connect("message::stream-collection", self.stream_collection_cb)
            #self.bus.connect("message::streams-selected", self.streams_selected_cb)
            pass
        else:
            #self.playbin.connect("video-changed", self.video_changed_cb)
            #self.playbin.connect("audio-changed", self.audio_changed_cb)
            #self.playbin.connect("text-changed", self.subtitle_changed_cb)

            #self.playbin.connect("video-tags-changed", self.video_tags_changed_cb)
            #self.playbin.connect("audio-tags-changed", self.audio_tags_changed_cb)
            #self.playbin.connect("text-tags-changed", self.subtitle_tags_changed_cb)
            pass

        #self.playbin.connect("notify::volume", self.volume_notify_cb)
        #self.playbin.connect("notify::mute", self.mute_notify_cb)
        #self.playbin.connect("source-setup", self.source_setup_cb)

        self.target_state = Gst.State.NULL
        self.current_state = Gst.State.NULL
        self.change_state(PlayerState.STOPPED)
        self.buffering = 100
        self.is_eos = False
        self.is_live = False
        self.rate = 1.0

        print("Starting main loop")
        self.loop.run()
        print("Stopped main loop")

        #bus_source.destroy()
        #bus_source.unref()
        self.bus.remove_signal_watch()
        #self.bus.unref()
        self.bus = None

        self.remove_tick_source()
        self.remove_ready_timeout_source()

        self.lock.lock()
        #if self.media_info:
        #     g_object_unref (self.media_info)
        #     self.media_info = NULL

        self.remove_seek_source()
        self.lock.unlock()

        self.context.pop_thread_default()

        self.target_state = Gst.State.NULL
        self.current_state = Gst.State.NULL
        if self.playbin:
            self.playbin.set_state(Gst.State.NULL)
            #self.playbin.unref()
            self.playbin = None

        print("Stopped main thread")

    def play_internal(self):
        print("Play")

        self.lock.lock()
        if not self.uri:
            self.lock.unlock()
            return GLib.SOURCE_REMOVE
        self.lock.unlock()

        self.remove_ready_timeout_source()
        self.target_state = Gst.State.PLAYING

        if self.current_state < Gst.State.PAUSED:
            self.change_state(PlayerState.BUFFERING)

        if self.current_state >= Gst.State.PAUSED and not self.is_eos and self.buffering == 100 and not (self.seek_position != Gst.CLOCK_TIME_NONE or self.seek_pending):
            state_ret = self.playbin.set_state(Gst.State.PLAYING)
        else:
            state_ret = self.playbin.set_state(Gst.State.PAUSED)

        if state_ret == Gst.StateChangeReturn.FAILURE:
            print("Failed to play")
            return
        elif state_ret == Gst.StateChangeReturn.NO_PREROLL:
            print("Pipeline is live")
            self.is_live = True

        if self.is_eos:
            print("Was EOS, seeking to beginning")
            self.is_eos = False
            ret = Gst.Element.seek_simple(self.playbin, Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            if not ret:
                print("Seek to beginning failed")
                self.stop_internal(True)
                self.play_internal()

        return GLib.SOURCE_REMOVE

    def play(self):
        self.lock.lock()
        self.inhibit_sigs = False
        self.lock.unlock()

        self.context.invoke_full(GLib.PRIORITY_DEFAULT, self.play_internal)

    def pause_internal(self):
        print("Pause")

        self.lock.lock()
        if not self.uri:
            self.lock.unlock()
            return GLib.SOURCE_REMOVE
        self.lock.unlock()

        self.tick_cb()
        self.remove_tick_source()
        self.remove_ready_timeout_source()

        self.target_state = Gst.State.PAUSED

        if self.current_state < Gst.State.PAUSED:
            self.change_state(PlayerState.BUFFERING)

        # HACK
        env = os.getenv("GST_PLAYER_PAUSE_SEEK_HACK")
        if env and env.startswith("1"):
            # this breaks stop() when done as pause() + seek(0)
            self.seek_position = self.get_position()
        # HACK

        state_ret = self.playbin.set_state(Gst.State.PAUSED)
        if state_ret == Gst.StateChangeReturn.FAILURE:
            print("Failed to pause")
            return
        elif state_ret == Gst.StateChangeReturn.NO_PREROLL:
            print("Pipeline is live")
            self.is_live = True

        if self.is_eos:
            print("Was EOS, seeking to beginning")
            self.is_eos = False

            ret = Gst.Element.seek_simple(self.playbin, Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            if not ret:
                print("Seek to beginning failed")
                self.stop_internal(True)
                self.pause_internal()

        return GLib.SOURCE_REMOVE

    def pause(self):
        self.lock.lock()
        self.inhibit_sigs = False
        self.lock.unlock()

        self.context.invoke_full(GLib.PRIORITY_DEFAULT, self.pause_internal)

    def stop_internal(self, transient):
        print("Stop (transient %s)" % transient)

        self.tick_cb()
        self.remove_tick_source()

        self.add_ready_timeout_source()

        self.target_state = Gst.State.NULL
        self.current_state = Gst.State.READY
        self.is_live = False
        self.is_eos = False

        Gst.Bus.set_flushing(self.bus, True)
        self.playbin.set_state(Gst.State.READY)
        Gst.Bus.set_flushing(self.bus, False)

        self.change_state(PlayerState.BUFFERING if transient and self.app_state != PlayerState.STOPPED else PlayerState.STOPPED)

        self.buffering = 100
        self.cached_duration = Gst.CLOCK_TIME_NONE

        self.lock.lock()
        if self.media_info:
            self.media_info = None
        self.seek_pending = False
        self.remove_seek_source()
        self.seek_position = Gst.CLOCK_TIME_NONE
        self.last_seek_time = -1
        self.rate = 1.0
        self.lock.unlock()

    def stop_internal_dispatch(self):
        self.stop_internal(False)

        return GLib.SOURCE_REMOVE

    def stop(self):
        self.lock.lock()
        self.inhibit_sigs = True
        self.lock.unlock()

        self.context.invoke_full(GLib.PRIORITY_DEFAULT, self.stop_internal_dispatch)

    def about_to_finish(self, playbin, user_data=None):
        print("*** ABOUT TO FINISH ***")
        print(playbin.get_name())
        print(user_data)

    # Must be called with lock from main context, releases lock!
    def seek_internal_locked(self):
        self.remove_seek_source()

        # only seek in PAUSED
        if self.current_state < Gst.State.PAUSED:
            return
        elif self.current_state != Gst.State.PAUSED:
            self.lock.unlock()
            #self.target_state = self.current_state
            state_ret = self.playbin.set_state(Gst.State.PAUSED)
            if state_ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to seek")
                #emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED, "Failed to seek"))
                self.lock.lock()
                return
            self.lock.lock()
            return
        self.last_seek_time = Gst.util_get_timestamp()
        position = self.seek_position
        self.seek_position = Gst.CLOCK_TIME_NONE
        self.seek_pending = True
        rate = self.rate

        self.lock.unlock()

        self.remove_tick_source()
        self.is_eos = False

        flags = Gst.SeekFlags.NONE
        flags |= Gst.SeekFlags.FLUSH

        if self.accurate:
            flags |= Gst.SeekFlags.ACCURATE
        #else:
        #    flags &= ~Gst.SeekFlags.ACCURATE

        if rate != 1.0:
            flags |= Gst.SeekFlags.TRICKMODE

        if rate >= 0.0:
            event = Gst.Event.new_seek(rate, Gst.Format.TIME, flags, Gst.SeekType.SET, position, Gst.SeekType.NONE, -1) #Gst.CLOCK_TIME_NONE)
        else:
            event = Gst.Event.new_seek(rate, Gst.Format.TIME, flags, Gst.SeekType.SET, 0, Gst.SeekType.SET, position)

        print("Seek with rate %s to %s" % (rate, position))

        ret = Gst.Element.send_event(self.playbin, event)
        if not ret:
            print("Failed to seek to %s" % position)
            #emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED,            "Failed to seek to %" GST_TIME_FORMAT, GST_TIME_ARGS (position)))
        self.lock.lock()

    def seek_internal(self, user_data=None):
        self.lock.lock()
        self.seek_internal_locked()
        self.lock.unlock()

        return GLib.SOURCE_REMOVE

    def seek(self, position):

        self.lock.lock()
        if not self.media_info.seekable:
            print("Media is not seekable")
            self.lock.unlock()
            return

        self.seek_position = position

        # If there is no seek being dispatch to the main context currently do that,
        # otherwise we just updated the seek position so that it will be taken by
        # the seek handler from the main context instead of the old one.
        if not self.seek_source:
            now = Gst.util_get_timestamp()

            # if no seek is pending or it was started more than the grace perios then seek
            # immediately, otherwise wait until the end of the grace period
            grace_period = self.GRACE_PERIOD * 1000 * 1000
            elapsed_since_last = now - self.last_seek_time
            if not self.seek_pending or (elapsed_since_last > grace_period):
                self.seek_source = GLib.idle_source_new()
                self.seek_source.set_callback(self.seek_internal)
                print("Dispatching seek to position %s" % position)
                self.seek_source.attach(self.context)
            else:
                # Note that last_seek_time must be set to something at this point and
                # it must be smaller than 250 mseconds
                delay = grace_period - elapsed_since_last
                self.seek_source = GLib.timeout_source_new(delay)
                self.seek_source.set_callback(self.seek_internal)
                print("Delaying seek to position %s by %s ms" % (position, delay))
                self.seek_source.attach(self.context)
        self.lock.unlock()

    def remove_seek_source(self):
        if not self.seek_source:
            return
        self.seek_source.destroy()
        #self.seek_source.unref()
        self.seek_source = None

    # https://github.com/Kurento/gstreamer/blob/master/tests/examples/stepping/framestep1.c
    def step(self):
        # only seek in PAUSED
        print("STEPPING")
        if self.current_state < Gst.State.PAUSED:
            return
        elif self.current_state != Gst.State.PAUSED:
            self.target_state = self.current_state
            state_ret = self.playbin.set_state(Gst.State.PAUSED)
            if state_ret == Gst.StateChangeReturn.FAILURE:
                print("Failed to seek")
                #emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED, "Failed to seek"))
                return
            return

        rate = self.rate

        # A rate of <= 0.0 is not allowed.
        # Pause the pipeline, for the effect of rate = 0.0 or first reverse the direction of playback using a seek event to get the same effect as rate < 0.0.
        if rate >= 0.0:
            event = Gst.Event.new_step(Gst.Format.BUFFERS, 1, rate, True, False)
        else:
            event = Gst.Event.new_step(Gst.Format.BUFFERS, 1, -rate, True, False)

        #GST_DEBUG_OBJECT (self, "Seek with rate %.2lf to %" GST_TIME_FORMAT,      rate, GST_TIME_ARGS (position))

        #ret = gst_element_send_event (self.playbin, s_event)
        #print("steppin")
        ret = Gst.Element.send_event(self.playbin, event)
        if not ret:
            print("Failed to seek to %s" % self.position)
            #emit_error (self, g_error_new (GST_PLAYER_ERROR, GST_PLAYER_ERROR_FAILED,            "Failed to seek to %" GST_TIME_FORMAT, GST_TIME_ARGS (position)))
            pass

    def step_backward(self):
        if self.current_state == Gst.State.PAUSED:
            if self.rate > 0:
                self.set_rate(-self.rate)
            else:
                self.step()

    def step_forward(self):
        if self.current_state == Gst.State.PAUSED:
            if self.rate < 0:
                self.set_rate(-self.rate)
            else:
                self.step()

    def log(self, msg, data):
        t = msg.type
        tn = Gst.MessageType.get_name(t)
        sn = msg.src.get_name()
        print('  %s - %s: %s' % (tn, sn, data))


def str_enum(enum):
    # examples:
    # <enum Gst.State.READY of type Gst.State>
    # <flags GST_MESSAGE_STREAM_STATUS of type Gst.MessageType>
    prog = re.compile("<.* (.*) of type (.*)>")
    m = prog.match(str(enum))
    if m:
        return m.group(1)
    return str(enum)
