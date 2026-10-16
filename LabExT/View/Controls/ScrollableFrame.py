#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LabExT  Copyright (C) 2021  ETH Zurich and Polariton Technologies AG
This program is free software and comes with ABSOLUTELY NO WARRANTY; for details see LICENSE file.
"""

from tkinter import Frame, Scrollbar, Canvas


class ScrollableFrame(Frame):

    def __init__(self, top, *args, **kwargs):
        Frame.__init__(self, top, *args, **kwargs)

        vscrollbar = Scrollbar(self, orient='vertical')
        vscrollbar.grid(row=0, column=1, sticky='ns')

        self.canvas = Canvas(self, yscrollcommand=vscrollbar.set)
        self.canvas.grid(row=0, column=0, sticky='nswe')

        vscrollbar.config(command=self.canvas.yview)

        # Make the canvas expandable
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Create the canvas contents
        self.frame = Frame(self.canvas)

        # add the content frame to the canvas
        self.canvas_frame = self.canvas.create_window(4, 4, window=self.frame, anchor='nw')

        # bind callbacks, needed to propagate size changes from canvas to frame
        self.frame.bind('<Configure>', self._frame_changed)
        self.canvas.bind('<Configure>', self._canvas_changed)

        # The wheel is bound while the pointer is over this frame and unbound when it leaves.
        # bind_all is what makes the wheel work over the child widgets too - a wheel event goes to
        # the widget under the pointer, which is a parameter entry rather than the canvas - and
        # scoping it to enter/leave is what stops one scrollable frame from stealing the wheel
        # from another, or from a listbox.
        self.bind('<Enter>', self._bind_mouse_wheel)
        self.bind('<Leave>', self._unbind_mouse_wheel)

    def get_content_frame(self):
        return self.frame

    def _bind_mouse_wheel(self, *args):
        self.bind_all('<MouseWheel>', self._mouse_wheel)     # Windows and macOS
        self.bind_all('<Button-4>', self._mouse_wheel)       # X11
        self.bind_all('<Button-5>', self._mouse_wheel)

    def _unbind_mouse_wheel(self, *args):
        self.unbind_all('<MouseWheel>')
        self.unbind_all('<Button-4>')
        self.unbind_all('<Button-5>')

    def _mouse_wheel(self, event):
        region = self.canvas.cget('scrollregion')
        if region:
            # Nothing to scroll when the content fits, and scrolling anyway would let the user
            # drag the content off the top of a short page with no way to see it has happened.
            _, top, _, bottom = (float(v) for v in region.split())
            if bottom - top <= self.canvas.winfo_height():
                return

        if event.num == 4:
            delta = -1
        elif event.num == 5:
            delta = 1
        else:
            # Windows reports the wheel in multiples of 120, macOS in single steps
            delta = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(delta, 'units')

    def _canvas_changed(self, *args):
        self.canvas.itemconfigure(self.canvas_frame, width=self.canvas.winfo_width())

    def _frame_changed(self, *args):
        # marcoep: unclear if this is needed
        self.frame.update_idletasks()
        x1, y1, x2, y2 = self.canvas.bbox("all")
        height = self.canvas.winfo_height()
        self.canvas.config(scrollregion=(0, 0, x2, max(y2, height)))
