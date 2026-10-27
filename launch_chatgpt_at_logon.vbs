Option Explicit

Dim shell, wmi, processes, attempt
Set shell = CreateObject("WScript.Shell")
On Error Resume Next
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
On Error GoTo 0

' The Windows AppX shell can take a while to become ready after the PIN sign-in.
' Retry only when ChatGPT is not already running, so this stays idempotent.
For attempt = 1 To 6
    WScript.Sleep 10000
    If Not wmi Is Nothing Then
        Set processes = wmi.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE Name='ChatGPT.exe'")
    End If
    If wmi Is Nothing Or processes Is Nothing Or processes.Count = 0 Then
        shell.Run "explorer.exe shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App", 0, False
    Else
        Exit For
    End If
Next

Set processes = Nothing
Set wmi = Nothing
Set shell = Nothing
