// Featherbox launcher: starts the local backend (bundled Python), opens the app window,
// and keeps a tray icon with "Open" / "Quit". A second launch just reopens the window.
// Written for the C# 5 compiler that ships with Windows (.NET Framework 4.x).
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows.Forms;

static class Launcher
{
    public static readonly string Root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    public static readonly int Port = ReadPort();
    public static string Url { get { return "http://127.0.0.1:" + Port; } }
    public static string DataDir
    {
        get
        {
            string env = Environment.GetEnvironmentVariable("FEATHERBOX_DATA");
            return string.IsNullOrEmpty(env) ? Path.Combine(Root, "data") : env;
        }
    }

    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();

    static int ReadPort()
    {
        int p;
        return int.TryParse(Environment.GetEnvironmentVariable("FEATHERBOX_PORT"), out p) ? p : 8420;
    }

    public static string QuitEventName { get { return "Featherbox.Quit." + Port; } }

    [STAThread]
    static void Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--quit") { AskRunningToQuit(); return; }
        try { SetProcessDPIAware(); } catch { }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool owner;
        using (var mutex = new Mutex(true, "Featherbox.Launcher." + Port, out owner))
        {
            if (!owner || Alive())
            {
                // Already running (tray icon exists, or a developer started the backend): just show the window.
                if (WaitReady(60)) OpenWindow();
                return;
            }
            // --background (after an update): start quietly; the window that was already open reconnects by itself.
            Application.Run(new TrayApp(args.Length > 0 && args[0] == "--background"));
        }
    }

    /// `Featherbox.exe --quit` (used by setup/uninstall): ask the running tray app to shut down cleanly.
    static void AskRunningToQuit()
    {
        EventWaitHandle ev;
        if (!EventWaitHandle.TryOpenExisting(QuitEventName, out ev)) return;   // not running
        using (ev) ev.Set();
        var until = DateTime.Now.AddSeconds(20);
        while (DateTime.Now < until)
        {
            Mutex m;
            if (!Mutex.TryOpenExisting("Featherbox.Launcher." + Port, out m)) return;   // tray app has exited
            m.Dispose();
            Thread.Sleep(300);
        }
    }

    public static bool Alive()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Url + "/api/system");
            req.Timeout = 1500;
            req.Proxy = null;
            using (var resp = (HttpWebResponse)req.GetResponse()) return resp.StatusCode == HttpStatusCode.OK;
        }
        catch { return false; }
    }

    public static bool WaitReady(int seconds)
    {
        var until = DateTime.Now.AddSeconds(seconds);
        while (DateTime.Now < until)
        {
            if (Alive()) return true;
            Thread.Sleep(400);
        }
        return false;
    }

    public static void OpenWindow()
    {
        // Edge "app mode" gives a clean window with no tabs or address bar; any browser works as a fallback.
        string[] edges = {
            Environment.ExpandEnvironmentVariables(@"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            Environment.ExpandEnvironmentVariables(@"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        };
        foreach (var e in edges)
        {
            if (!File.Exists(e)) continue;
            try { Process.Start(e, "--app=" + Url + " --window-size=1320,880"); return; } catch { }
        }
        try { Process.Start(Url); } catch { }
    }
}

class TrayApp : ApplicationContext
{
    readonly NotifyIcon tray = new NotifyIcon();
    readonly Form splash;
    readonly string token = Guid.NewGuid().ToString("N");
    readonly SynchronizationContext ui;
    Process server;
    bool quitting;

    readonly bool background;

    public TrayApp(bool background)
    {
        this.background = background;
        Icon icon = null;
        try { icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch { }
        splash = MakeSplash(icon);
        if (!background) splash.Show();
        ui = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();

        var menu = new ContextMenuStrip();
        menu.Items.Add("Open Featherbox", null, delegate { Launcher.OpenWindow(); });
        menu.Items.Add("Open data folder", null, delegate { Process.Start("explorer.exe", "\"" + Launcher.DataDir + "\""); });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Quit Featherbox", null, delegate { Quit(); });
        tray.Icon = icon ?? SystemIcons.Application;
        tray.Text = "Featherbox";
        tray.ContextMenuStrip = menu;
        tray.DoubleClick += delegate { Launcher.OpenWindow(); };

        try { StartServer(); }
        catch (Exception ex) { Fail("Could not start Featherbox:\n" + ex.Message); return; }

        var t = new Thread(WaitThenOpen);
        t.IsBackground = true;
        t.Start();

        var quitEvent = new EventWaitHandle(false, EventResetMode.AutoReset, Launcher.QuitEventName);
        var q = new Thread(delegate() { quitEvent.WaitOne(); RunOnUi(delegate { Quit(); }); });
        q.IsBackground = true;
        q.Start();
    }

    static Form MakeSplash(Icon icon)
    {
        var f = new Form
        {
            FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.CenterScreen,
            Size = new Size(380, 150), BackColor = Color.FromArgb(17, 19, 24), ShowInTaskbar = true,
            Text = "Featherbox", TopMost = true,
        };
        if (icon != null) f.Icon = icon;
        var pic = new PictureBox { Location = new Point(24, 34), Size = new Size(48, 48), SizeMode = PictureBoxSizeMode.Zoom };
        if (icon != null) pic.Image = new Icon(icon, 48, 48).ToBitmap();
        var title = new Label
        {
            Text = "Featherbox", ForeColor = Color.White, Font = new Font("Segoe UI Semibold", 15f),
            Location = new Point(86, 30), AutoSize = true,
        };
        var sub = new Label
        {
            Text = "Starting Featherbox…", ForeColor = Color.FromArgb(160, 168, 184), Font = new Font("Segoe UI", 9.5f),
            Location = new Point(88, 62), AutoSize = true,
        };
        var bar = new ProgressBar { Style = ProgressBarStyle.Marquee, Location = new Point(24, 108), Size = new Size(332, 6), MarqueeAnimationSpeed = 25 };
        f.Controls.AddRange(new Control[] { pic, title, sub, bar });
        return f;
    }

    void StartServer()
    {
        string py = Path.Combine(Launcher.Root, @"python\pythonw.exe");
        string backend = Path.Combine(Launcher.Root, @"app\backend");
        if (!File.Exists(py)) throw new FileNotFoundException("Missing " + py + ". Please reinstall Featherbox.");
        var psi = new ProcessStartInfo(py, "-X utf8 \"" + Path.Combine(backend, "run.py") + "\" --no-browser")
        {
            WorkingDirectory = backend, UseShellExecute = false, CreateNoWindow = true,
        };
        if (string.IsNullOrEmpty(Environment.GetEnvironmentVariable("FEATHERBOX_DATA")))
            psi.EnvironmentVariables["FEATHERBOX_DATA"] = Launcher.DataDir;
        psi.EnvironmentVariables["FEATHERBOX_SHUTDOWN_TOKEN"] = token;
        server = Process.Start(psi);
        server.EnableRaisingEvents = true;
        server.Exited += delegate
        {
            if (quitting) return;
            RunOnUi(delegate { Fail("Featherbox stopped unexpectedly.\n\n" + LogTail()); });
        };
    }

    void WaitThenOpen()
    {
        bool ok = false;
        var until = DateTime.Now.AddSeconds(180);
        while (DateTime.Now < until && !server.HasExited)
        {
            if (Launcher.Alive()) { ok = true; break; }
            Thread.Sleep(400);
        }
        RunOnUi(delegate
        {
            if (!ok)
            {
                if (!server.HasExited) Fail("Featherbox did not start within 3 minutes.\n\n" + LogTail());
                return;
            }
            splash.Close();
            tray.Visible = true;
            if (background)
            {
                tray.ShowBalloonTip(6000, "Featherbox was updated", "It's ready. Your open window reconnects by itself.", ToolTipIcon.Info);
                return;
            }
            Launcher.OpenWindow();
            tray.ShowBalloonTip(6000, "Featherbox is running",
                "It keeps running here while you work. Right-click this icon to quit.", ToolTipIcon.Info);
        });
    }

    void RunOnUi(MethodInvoker fn)
    {
        // The WinForms SynchronizationContext keeps working after the splash window is closed.
        ui.Post(delegate { fn(); }, null);
    }

    string LogTail()
    {
        try
        {
            string log = Path.Combine(Launcher.DataDir, @"logs\app.log");
            string text = File.ReadAllText(log);
            return text.Length > 1200 ? "…" + text.Substring(text.Length - 1200) : text;
        }
        catch { return "(no log)"; }
    }

    void Fail(string message)
    {
        quitting = true;
        try { splash.Hide(); } catch { }
        MessageBox.Show(message, "Featherbox", MessageBoxButtons.OK, MessageBoxIcon.Error);
        Shutdown();
        ExitThread();
    }

    void Quit()
    {
        quitting = true;
        tray.Visible = false;
        Shutdown();
        ExitThread();
    }

    void Shutdown()
    {
        if (server == null || server.HasExited) return;
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Launcher.Url + "/api/shutdown");
            req.Method = "POST";
            req.Proxy = null;
            req.Timeout = 3000;
            req.Headers["X-Featherbox-Token"] = token;
            req.ContentLength = 0;
            using (req.GetResponse()) { }
        }
        catch { }
        if (!server.WaitForExit(8000))
        {
            try { server.Kill(); } catch { }
        }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing) { tray.Dispose(); splash.Dispose(); }
        base.Dispose(disposing);
    }
}
