' Khoi dong Booking Deadline Bot o che do an (khong hien cua so den).
' Bot van hien icon o khay he thong.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
root = fso.GetParentFolderName(scriptDir)
pyw = root & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pyw) Then
  MsgBox "Chua cai dat Booking Deadline Bot." & vbCrLf & _
         "Hay chay scripts\install.ps1 truoc.", 16, "Booking Deadline Bot"
  WScript.Quit 1
End If

Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run """" & pyw & """ -m booking_bot run", 0, False
