# from LabExT.View.Controls.Wizard import Wizard

# class MyWizard(Wizard):
#     def __init__(self, parent):
#         super().__init__(
#             parent, # required
#             width=800, # Default: 640
#             height=600, # Default: 480
#             with_sidebar=True, # Default: True
#             with_error=True, # Default: True
#             on_cancel=self._cancel, # not required
#             on_finish=self._save, # not required
#             next_button_label="Next Step" # Default: "Next Step"
#             previous_button_label="Previous Step", # Default: Previous Step
#             cancel_button_label="Cancel and Close", # Default: Cancel
#             finish_button_label="Finish and Save"
#         )
#         self.connection_step = self.add_step(
#             builder=self._connection_step_builder, # required
#             title="Stage Connection", # Default: None
#             on_next=self._on_next, # not required
#             on_previous=self._on_previous, # not required
#             on_reload=self._on_reload, # not required
#             previous_step_enabled=True, # Default: True
#             next_step_enabled=True, # Default: True
#             finish_step_enabled=False # Default: False
#         )
#         def _check_assignment(self):
#             if is_stage_assignment_valid(self._current_stage_assignment):
#                 self.current_step.next_step_enabled = True
#                 self.set_error("")
#             else:
#                 self.current_step.next_step_enabled = False
#                 self.set_error("Please assign at least one stage and do not select a stage twice.")
        


         
        