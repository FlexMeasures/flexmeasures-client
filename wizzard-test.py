import asyncio
import queue
import sys
import textwrap
import threading
import tkinter as tk
from io import StringIO
from tkinter import messagebox, scrolledtext, ttk


class QueueIO(StringIO):
    """A file-like object that writes to a queue."""

    def __init__(self, queue_instance, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = queue_instance

    def write(self, s):
        self.queue.put(s)

    def flush(self):
        pass


class FlexMeasuresTutorial(tk.Tk):
    """
    An interactive GUI tutorial for the flexmeasures-client
    with editable code blocks and a forced light theme.
    """

    def __init__(self):
        super().__init__()
        self.title("Interactive FlexMeasures Client Tutorial")
        self.geometry("900x800")

        # --- Color and Style Setup ---
        self.BG_COLOR = "#ECECEC"
        self.TEXT_COLOR = "#000000"
        self.WIDGET_BG_COLOR = "#FFFFFF"
        self.CODE_BG_COLOR = "#FFFFFF"  # Make code box white and editable
        self.setup_style()

        # --- State Management ---
        self.script_globals = {}
        self.output_queue = queue.Queue()
        self.is_running_code = False
        self.current_code_widget = None  # Holds the active code box

        # --- GUI Setup ---
        self.step = 0
        self.steps_config = [
            {
                "title": "Step 1: Creating an Account",
                "content_func": self.create_step1,
                "code": self.get_step1_code(),
            },
            {
                "title": "Step 2: Create Building Asset & Sensors",
                "content_func": self.create_step2,
                "code": self.get_step2_code(),
            },
            {
                "title": "Step 3: Add Price Data",
                "content_func": self.create_step3,
                "code": self.get_step3_code(),
            },
            {
                "title": "Step 4: Create a Child PV Asset",
                "content_func": self.create_step4,
                "code": self.get_step4_code(),
            },
            {
                "title": "Step 5: Review",
                "content_func": self.create_step5,
                "code": None,
            },
        ]

        self.create_widgets()
        self.show_step()
        self.after(100, self.process_output_queue)

    def setup_style(self):
        """Configures ttk styles to enforce a light theme."""
        self.configure(bg=self.BG_COLOR)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            style.theme_use("default")

        style.configure(".", background=self.BG_COLOR, foreground=self.TEXT_COLOR)
        style.configure("TFrame", background=self.BG_COLOR)
        style.configure(
            "TButton", background="#DDDDDD", foreground=self.TEXT_COLOR, borderwidth=1
        )
        style.map("TButton", background=[("active", "#CFCFCF")])
        style.configure("TLabel", background=self.BG_COLOR, foreground=self.TEXT_COLOR)
        style.configure(
            "TLabelframe", background=self.BG_COLOR, foreground=self.TEXT_COLOR
        )
        style.configure(
            "TLabelframe.Label", background=self.BG_COLOR, foreground=self.TEXT_COLOR
        )

    def create_widgets(self):
        """Creates the main GUI frames, buttons, and console."""
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.content_frame = ttk.Frame(main_frame)
        self.content_frame.pack(fill=tk.BOTH, expand=True)

        navigation_frame = ttk.Frame(main_frame)
        navigation_frame.pack(fill=tk.X, pady=10, side=tk.BOTTOM)

        self.prev_button = ttk.Button(
            navigation_frame, text="Previous", command=self.prev_step
        )
        self.prev_button.pack(side=tk.LEFT)

        self.next_button = ttk.Button(
            navigation_frame, text="Next", command=self.next_step
        )
        self.next_button.pack(side=tk.RIGHT)

        console_frame = ttk.LabelFrame(main_frame, text="Output Console", padding="5")
        console_frame.pack(fill=tk.BOTH, expand=True, side=tk.BOTTOM, pady=(10, 0))
        self.output_console = scrolledtext.ScrolledText(
            console_frame,
            wrap=tk.WORD,
            height=10,
            state="disabled",
            bg="#F5F5F5",
            fg=self.TEXT_COLOR,
        )
        self.output_console.pack(fill=tk.BOTH, expand=True)

    def process_output_queue(self):
        """Checks for and displays new output from the running code thread."""
        try:
            while True:
                line = self.output_queue.get_nowait()
                self.output_console.config(state="normal")
                self.output_console.insert(tk.END, line)
                self.output_console.see(tk.END)
                self.output_console.config(state="disabled")
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_output_queue)

    def show_step(self):
        """Clears the content frame and displays the current step."""
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.current_code_widget = None  # Reset the code widget reference

        step_config = self.steps_config[self.step]
        frame = self.create_step_frame(step_config["title"])
        step_config["content_func"](frame)

        if step_config["code"]:
            self.current_code_widget = self.add_code_block(frame, step_config["code"])
            run_button = ttk.Button(
                frame, text="▶ Run Code Below", command=self.trigger_code_execution
            )
            run_button.pack(pady=5, anchor="w")

        self.update_buttons()

    def trigger_code_execution(self):
        """Gets code from the active widget and runs it."""
        if self.current_code_widget:
            code_to_run = self.current_code_widget.get("1.0", tk.END)
            self.run_code(code_to_run)
        else:
            messagebox.showerror("Error", "No code widget found for this step.")

    def run_code(self, code_to_run):
        """Executes a string of Python code in a separate thread."""
        if self.is_running_code:
            messagebox.showwarning(
                "Busy", "A process is already running. Please wait for it to complete."
            )
            return

        self.is_running_code = True
        self.clear_console()
        self.log_to_console("Running code...\n")

        def code_runner():
            try:
                sys.stdout = QueueIO(self.output_queue)
                sys.stderr = sys.stdout
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                exec(
                    f"async def __main():\n{textwrap.indent(code_to_run, '    ')}",
                    self.script_globals,
                )
                loop.run_until_complete(self.script_globals["__main"]())
            except Exception as e:
                print(f"\nAn error occurred:\n{e}")
            finally:
                sys.stdout = sys.__stdout__
                sys.stderr = sys.__stderr__
                self.output_queue.put("\n--- Execution Finished ---\n")
                self.is_running_code = False

        threading.Thread(target=code_runner, daemon=True).start()

    # --- GUI helper methods (navigation, logging, etc.) ---
    def log_to_console(self, text):
        self.output_console.config(state="normal")
        self.output_console.insert(tk.END, text)
        self.output_console.see(tk.END)
        self.output_console.config(state="disabled")

    def clear_console(self):
        self.output_console.config(state="normal")
        self.output_console.delete(1.0, tk.END)
        self.output_console.config(state="disabled")

    def next_step(self):
        if self.is_running_code:
            messagebox.showwarning(
                "Busy", "Please wait for the current process to finish."
            )
            return
        if self.step < len(self.steps_config) - 1:
            self.step += 1
            self.show_step()
        else:
            if messagebox.askokcancel(
                "Finish", "You have completed the tutorial. Close the application?"
            ):
                self.destroy()

    def prev_step(self):
        if self.is_running_code:
            messagebox.showwarning(
                "Busy", "Please wait for the current process to finish."
            )
            return
        if self.step > 0:
            self.step -= 1
            self.show_step()

    def update_buttons(self):
        self.prev_button["state"] = tk.NORMAL if self.step > 0 else tk.DISABLED
        self.next_button["text"] = (
            "Finish" if self.step == len(self.steps_config) - 1 else "Next"
        )

    def create_step_frame(self, title):
        frame = ttk.Frame(self.content_frame, padding="10")
        frame.pack(fill=tk.BOTH, expand=True)
        title_label = ttk.Label(frame, text=title, font=("Helvetica", 16, "bold"))
        title_label.pack(pady=(0, 10), anchor="w")
        return frame

    def add_rich_text(self, parent, text_content):
        text_widget = tk.Text(
            parent,
            wrap=tk.WORD,
            relief=tk.FLAT,
            height=3,
            bg=self.BG_COLOR,
            fg=self.TEXT_COLOR,
            borderwidth=0,
        )
        text_widget.tag_configure("bold", font=("Helvetica", 10, "bold"))
        parts = text_content.split("**")
        for i, part in enumerate(parts):
            text_widget.insert(tk.END, part, "bold" if i % 2 == 1 else "")
        text_widget.configure(state="disabled")
        text_widget.pack(pady=5, fill=tk.X, anchor="w")

    def add_code_block(self, parent, code):
        """Adds a scrollable, EDITABLE code block and returns it."""
        code_text = scrolledtext.ScrolledText(
            parent,
            wrap=tk.WORD,
            height=12,
            width=80,
            bg=self.CODE_BG_COLOR,
            fg=self.TEXT_COLOR,
            insertbackground=self.TEXT_COLOR,  # Make cursor visible
        )
        code_text.insert(tk.INSERT, textwrap.dedent(code).strip())
        # The widget is now editable by default
        code_text.pack(pady=5, fill=tk.BOTH, expand=True, anchor="w")
        return code_text

    # --- Step Content & Code Definitions ---

    def get_step1_code(self):
        return """# This is informational. Run these commands in your terminal.
# You cannot run shell commands from this GUI.

# In your terminal, in the flexmeasures repository root directory:
flexmeasures add toy-account
"""

    def create_step1(self, frame):
        self.add_rich_text(
            frame,
            "Before using the client, you need an account on a FlexMeasures server. The code box below shows the commands to create a local 'toy' account for development.",
        )

    def get_step2_code(self):
        return """from flexmeasures_client import FlexMeasuresClient
import asyncio

# --- Connection Details (EDIT THESE) ---
EMAIL = "admin@mycompany.io"
PASSWORD = "toy-password"
HOST = "localhost:5000"

async def create_asset_with_sensor(client):
    asset = await client.add_asset(
        name=asset_name,
        latitude=40,
        longitude=50,
        generic_asset_type_id=2,
        account_id=2,
    )

    sensor = await client.add_sensor(
        name=sensor_name,
        event_resolution="PT1H",
        unit="kW",
        generic_asset_id=asset.get("id"),
    )

    asset = await client.update_asset(
        asset_id=asset["id"],
        updates={
            "flex_context": {"site-consumption-capacity": "100 kW"},  # test this also
            "sensors_to_show": [{"title": "My Graph", "sensors": [sensor["id"]]}],
        },
    )
    return asset, sensor


async def main():
    client = FlexMeasuresClient(email=usr, password=pwd)

    asset = None
    sensor = None

    assets = await client.get_assets()
    for sst in assets:
        if sst["name"] == asset_name:
            asset = sst
            break

    if not asset:
        print("Creating asset with sensor ...")
        asset, sensor = await create_asset_with_sensor(client)
    else:
        answer = input(f"Asset '{asset_name}' already exists. Re-create?")
        if answer.lower() in ["y", "yes"]:
            await client.delete_asset(asset_id=asset["id"])
            asset, sensor = await create_asset_with_sensor(client)
        else:  # find sensor
            sensors = await client.get_sensors(asset_id=asset["id"])
            for snsr in sensors:
                if snsr["name"] == sensor_name:
                    sensor = snsr
                    break

    print(f"Asset ID: {asset['id']}")
    print(f"Sensor ID: {sensor['id']}")

    await client.post_measurements(
        sensor_id=sensor["id"],
        start="2025-07-07T04:00:00+02:00",
        duration="PT4H",
        values=[4.5, 7, 8.3, 1],
        unit="kW",
    )

    await client.close()


asyncio.run(main())
"""

    def create_step2(self, frame):
        self.add_rich_text(
            frame,
            "An **asset** is a physical object. **Sensors** record data for an asset. **You can now edit the code below!** Change the `EMAIL`, `PASSWORD`, and `HOST` to match your setup, then click 'Run'.",
        )

    def get_step3_code(self):
        return """import datetime as dt

print("Posting price data...")
# The 'client' and 'price_sensor' variables from Step 2 are remembered.
price_data = [120.5, 122.3, 119.8, 125.0, 130.2, 128.7, 135.5, 140.0]
start_time = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:00:00Z")

await client.post_measurements(
    sensor_id=price_sensor['id'],
    start=start_time,
    values=price_data,
    unit="EUR/MWh"
)
print(f"Successfully posted {len(price_data)} price measurements.")
"""

    def create_step3(self, frame):
        self.add_rich_text(
            frame,
            "This step posts example price data to the **price sensor** created previously. Feel free to change the `price_data` list to your own values.",
        )

    def get_step4_code(self):
        return """print("Creating PV asset...")
# The 'client', 'account_id', and 'building_asset' are remembered.
pv_asset = await client.add_asset(
    name="Rooftop PV",
    latitude=52.37, longitude=4.89,
    generic_asset_type_name="solar",
    account_id=account_id,
    parent_asset_id=building_asset['id']
)
print(f"Created PV asset with ID: {pv_asset['id']}")

pv_production_sensor = await client.add_sensor(
    name="PV Production", unit="MW", event_resolution="PT15M",
    generic_asset_id=pv_asset['id']
)
print(f"Created PV production sensor with ID: {pv_production_sensor['id']}")

# Finally, close the client connection
await client.close()
print("Client connection closed.")
"""

    def create_step4(self, frame):
        self.add_rich_text(
            frame,
            "Here, we add a PV installation as a **child** of the building **asset** to create a hierarchy. At the end, the client connection is closed.",
        )

    def create_step5(self, frame):
        self.add_rich_text(
            frame,
            "You have now interactively run all the steps! You've connected to the client, created assets and sensors (editing the code as you went), and posted data. You can now adapt the code from these steps for your own applications. ✅",
        )


if __name__ == "__main__":
    app = FlexMeasuresTutorial()
    app.mainloop()
