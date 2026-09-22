' Khoi dong Booking Control o che do an (khong hien cua so den):
' web noi bo + Automation Engine. Mo trinh duyet tai http://127.0.0.1:8765
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)
pyw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pyw) Then
  MsgBox "Chua cai dat Booking Control." & vbCrLf & _
         "Hay chay scripts\install.ps1 truoc.", 16, "Booking Control"
  WScript.Quit 1
End If

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run """" & pyw & """ -m booking_control run", 0, False
