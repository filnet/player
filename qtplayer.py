import sys
import argparse
import os
import glob

from player import Player, PlayerState

import gi
from gi.repository import GObject
from gi.repository import Gst, GstVideo

from PyQt5.QtCore import Qt, QObject, QEvent, QTime, QTimer
from PyQt5.QtCore import QMargins
from PyQt5.QtCore import pyqtSignal

from PyQt5.QtGui import QPixmap, QIcon

from PyQt5.QtWidgets import QApplication, QWidget, QSizePolicy, QStyle, QProxyStyle
from PyQt5.QtWidgets import QHBoxLayout, QVBoxLayout, QSpacerItem
from PyQt5.QtWidgets import QLabel, QPushButton, QToolButton, QSlider, QStyleOptionSlider

class TrackerStyle(QProxyStyle):

    def __init__(self, style):
        super().__init__(style)

    def styleHint(self, hint, option, widget, returnData):
        if hint == QStyle.SH_Slider_AbsoluteSetButtons:
            return (Qt.LeftButton | Qt.MidButton | Qt.RightButton)
        return super().styleHint(hint, option, widget, returnData)


class TrackerWidget(QSlider):

    def __init__(self):
        super().__init__(Qt.Horizontal)
        super().setStyle(TrackerStyle(self.style()))
        super().setTracking(False)
        super().setSingleStep(1000)
        super().setPageStep(5000)

    def setValue(self, value):
        if super().isSliderDown():
            position = super().sliderPosition()
        super().setValue(value)
        if super().isSliderDown():
            super().setSliderPosition(position)


class CommandWidget(QWidget):

    def __init__(self):
        super().__init__(Qt.Horizontal)


class VideoWidget(QWidget):

    def __init__(self):
        super().__init__()
        palette = self.palette()
        palette.setColor(self.backgroundRole(), Qt.black)
        self.setPalette(palette)
        self.has_overlay = False
        self.use_overlay = False
        self.is_playing = False
        self.overlay = None

    def set_is_playing(self, playing):
        if self.is_playing == playing:
            return
        self.is_playing = playing

    def set_use_overlay(self, use):
        if self.use_overlay == use:
            return
        self.use_overlay = use
        self.enable_overlay(self.use_overlay and self.has_overlay)

    def set_overlay(self, overlay):
        if self.overlay:
            self.overlay.set_window_handle(None)
            self.has_overlay = False
        self.overlay = overlay
        if self.overlay:
            self.overlay.set_window_handle(self.winId())
            self.has_overlay = True
        self.enable_overlay(self.use_overlay and self.has_overlay)

    def enable_overlay(self, enable):
        # no need to auto fill background (i.e. paint) when overlay is enabled
        self.setAutoFillBackground(not enable)
        # overlay will (should) repaint the whole surface, so tell widget
        # not sure why it is needed in addition to disabling auto fill
        # but it fixes flickering when resizing
        self.setAttribute(Qt.WA_OpaquePaintEvent, enable)
        # disable double buffering to avoid flickering when resizing
        # for this to work we also need to override paintEngine() and make it return NULL.
        # see http://qt-project.org/faq/answer/how_does_qtwa_paintonscreen_relate_to_the_backing_store_widget_composition_
        # drawback is that this widget won't participate in composition...
        self.setAttribute(Qt.WA_PaintOnScreen, enable)
        if enable:
            self.overlay.expose()
        else:
            # need to trigger a repaint
            self.update()

    def paintEvent(self, event):
        #print("paint")
        if self.use_overlay and self.has_overlay:
            # no need to expose when playing (sink should repaint)
            # not sure videos with stills will work fine,
            # thus this "feature" is not implement
            # TODO port to GCS if it works
            if not self.is_playing:
                self.overlay.expose()
        else:
            super().paintEvent(event)

    def resizeEvent(self, event):
        #print("resize")
        if self.use_overlay and self.has_overlay:
            # see paintEvent()
            if not self.is_playing:
                self.overlay.expose()
        else:
            super().resizeEvent(event)

    def paintEngine(self):
        #print("paintEngine")
        try:
            if self.use_overlay and self.has_overlay:
                return None
            return super().paintEngine()
        except NameError:
            # super constructor calls paintEngine() too early
            return super().paintEngine()


class Main(QObject):

    def __init__(self, args):
        super().__init__()

        self.ignore_tracker = False
        self.file_index = 0

        app = QApplication(sys.argv)

        self.create_window(args)

        self.player = Player(args)

        self.player.state_changed.connect(self.update_state)
        self.player.duration_changed.connect(self.update_tracker_duration)
        self.player.position_updated.connect(self.update_tracker_position)

        self.tracker.valueChanged.connect(self.tracker_value_changed)

        #self.play_button.clicked.connect(functools.partial(self.button_clicked, "play"))
        self.play_button.clicked.connect(self.play)
        self.pause_button.clicked.connect(self.pause)
        self.stop_button.clicked.connect(self.stop)
        self.seek_backward_button.clicked.connect(self.player.step_backward)
        self.seek_forward_button.clicked.connect(self.player.step_forward)
        self.media_next_button.clicked.connect(self.next_media)
        self.media_previous_button.clicked.connect(self.previous_media)

        self.window.show()

        self.video_widget.set_overlay(self.player.playbin)

        self.update_state(PlayerState.STOPPED)

        self.files = [x.replace("\n", " ") for x in args.files]

        files = []
        for index, file in enumerate(self.files):
            print((index, file))
            if os.path.isdir(file):
                pattern = "*.wmv"
                files.extend(glob.iglob(file + "/**/" + pattern, recursive=True))
            elif os.path.isfile(file):
                files.append(file)
        self.files = files

        if self.file_index >= 0 and self.file_index < len(self.files):
            uri = Gst.filename_to_uri(self.files[self.file_index])
            self.player.set_uri(uri)
        #self.player.set_property("prop_gint", 12)

        self.state = None

        self.player.pause()

        ret = app.exec_()

        self.player.dispose()
        self.player = None

        #sys.exit(ret)

    def previous_media(self):
        index = (len(self.files) + self.file_index - 1) % len(self.files)
        self.goto_media(index)

    def next_media(self):
        index = (self.file_index + 1) % len(self.files)
        self.goto_media(index)

    def goto_media(self, index):
        self.file_index = index
        uri = Gst.filename_to_uri(self.files[self.file_index])
        self.player.set_uri(uri)
        self.state = None
        self.player.pause()

    def play(self):
        if self.state == PlayerState.PLAYING:
            self.player.pause()
            self.player.seek(0)
            self.player.play()
        else:
            self.player.play()

    def pause(self):
        if self.state == PlayerState.PLAYING:
            self.player.pause()
        elif self.state == PlayerState.PAUSED:
            self.player.play()

    def stop(self):
        if self.state == PlayerState.PLAYING:
            self.player.pause()
            self.player.seek(0)
        else:
            self.player.stop()

    def update_state(self, state):
        self.state = state

        enabled = state == PlayerState.PAUSED or state == PlayerState.PLAYING

        self.video_widget.set_use_overlay(enabled)
        self.video_widget.set_is_playing(state == PlayerState.PLAYING)

        self.play_button.setEnabled(True)
        #self.play_button.setVisible(state == PlayerState.STOPPED or state == PlayerState.PAUSED)
        self.pause_button.setEnabled(enabled)
        #self.pause_button.setVisible(state == PlayerState.PLAYING or state == PlayerState.BUFFERING)

        self.stop_button.setEnabled(enabled)

        self.tracker.setEnabled(True)

        if not enabled:
            self.update_tracker_duration(0)
        else:
            duration = self.player.get_duration()
            self.update_tracker_duration(duration)

    def update_tracker_duration(self, duration):
        self.ignore_tracker = True
        self.tracker.setRange(0, as_ticks(duration))
        self.ignore_tracker = False

    def update_tracker_position(self, position):
        self.ignore_tracker = True
        self.tracker.setValue(as_ticks(position))
        self.ignore_tracker = False

    def tracker_value_changed(self, value):
        if self.ignore_tracker:
            return
        self.player.set_position(as_time(value))

    def create_window(self, args):
        self.window = QWidget()
        self.window.setWindowTitle("Player")
        self.window.setGeometry(100, 100, 640, 580)

        # video window
        self.video_widget = VideoWidget()

        size_policy = self.video_widget.sizePolicy()
        size_policy.setHorizontalPolicy(QSizePolicy.Expanding)
        size_policy.setVerticalPolicy(QSizePolicy.Expanding)
        self.video_widget.setSizePolicy(size_policy)

        # tracker
        self.tracker = TrackerWidget()
        self.tracker.setTracking(args.tracking)
        #self.tracker.valueChanged.connect(lambda value: print("%s / %s" % (value, self.tracker.maximum())))
        #self.tracker.sliderMoved.connect(lambda value: print("*** %s / %s" % (value, self.tracker.maximum())))

        # buttons
        self.play_button = self.create_button("media-playback-start")
        self.pause_button = self.create_button("media-playback-pause")
        self.stop_button = self.create_button("media-playback-stop")
        self.seek_backward_button = self.create_button("media-seek-backward")
        self.seek_forward_button = self.create_button("media-seek-forward")
        self.skip_backward_button = self.create_button("media-skip-backward")
        self.skip_forward_button = self.create_button("media-skip-forward")
        self.media_previous_button = self.create_button("go-previous")
        self.media_next_button = self.create_button("go-next")

        # buttons window
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(QMargins(5, 5, 2, 2))
        button_layout.setSpacing(5)

        button_layout.addWidget(self.play_button)
        button_layout.addWidget(self.pause_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addWidget(self.seek_backward_button)
        button_layout.addWidget(self.seek_forward_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.media_previous_button)
        button_layout.addWidget(self.media_next_button)

        buttons_window = QWidget()
        buttons_window.setLayout(button_layout)

        palette = buttons_window.palette()
        palette.setColor(buttons_window.backgroundRole(), Qt.black)
        buttons_window.setPalette(palette)
        buttons_window.setAutoFillBackground(True)

        # layout
        layout = QVBoxLayout()
        layout.setContentsMargins(QMargins(0, 0, 0, 0))
        layout.setSpacing(0)

        layout.addWidget(self.video_widget)
        layout.addWidget(self.tracker)
        layout.addWidget(buttons_window)

        self.window.setLayout(layout)

    # TODO move to a utility/skinner
    def create_button(self, name):
        pixmap = QPixmap("D:/OpenPilotDev/tango-icon-theme-0.8.90/32x32/actions/" + name + ".png")
        icon = QIcon(pixmap)

        button = QPushButton()
        button.setIcon(icon)
        button.setIconSize(pixmap.rect().size())
        button.setContentsMargins(QMargins(0, 0, 0, 0))
        #setStyleSheet as border: none
        #button.setFlat(True)
        #button.setAutoRaise(True)

        palette = button.palette()
        palette.setColor(button.backgroundRole(), Qt.black)
        button.setPalette(palette)

        return button


def as_ticks(time):
    return int(time / 1000 / 1000)

def as_time(ticks):
    return int(ticks * 1000 * 1000)


parser = argparse.ArgumentParser(description='Player.')
parser.add_argument('files', metavar='FILE', type=str, nargs='+', help='a file to play')
parser.add_argument('--mute', action='store_true', help='mute sound')
parser.add_argument('--tracking', action='store_true', help='slider tracking')
parser.add_argument('--fast-seek', action='store_true', help='mute sound')
#parser.add_argument('-v', '--verbose', action='store_true', help='enable verbose mode')
#parser.add_argument('-d', '--dest', type=str, default='.', help='destination directory (defaults to current directory)')
#parser.add_argument('-f', '--files', metavar='FILE', nargs='+', required=True, help='a file')
#parser.add_argument('-e', '--excludes', metavar='FILE', nargs='+', help='a file')

args = parser.parse_args()

GObject.threads_init()
Gst.init(None)
Main(args)
