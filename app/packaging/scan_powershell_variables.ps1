# The fresh -NoProfile child's built-in variable options are the authority.
$readOnlyNames = @(Get-Variable | Where-Object { $_.Options -match 'ReadOnly|Constant' } | ForEach-Object { $_.Name.ToLowerInvariant() })
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$specialNames = @('input', 'args', '_', 'psitem', 'matches', 'this', 'sender', 'event', 'eventargs', 'eventsubscriber', 'foreach', 'switch', 'myinvocation', 'psboundparameters', 'pscmdlet', 'stacktrace', 'psscriptroot', 'pscommandpath')
$protectedNames = @($readOnlyNames + $specialNames | Sort-Object -Unique)
$blocks = [Console]::In.ReadToEnd() | ConvertFrom-Json
$findings = New-Object 'System.Collections.Generic.List[object]'

function Get-WriteTargets([Management.Automation.Language.Ast]$Target) {
	if ($Target -is [Management.Automation.Language.VariableExpressionAst]) {
		$Target
	} elseif ($Target -is [Management.Automation.Language.ConvertExpressionAst] -or $Target -is [Management.Automation.Language.AttributedExpressionAst]) {
		Get-WriteTargets $Target.Child
	} elseif ($Target -is [Management.Automation.Language.ArrayLiteralAst]) {
		foreach ($element in $Target.Elements) { Get-WriteTargets $element }
	}
	# Member/index assignments change an object, not the variable's binding.
}

function Add-Finding([string]$Label, [string]$Kind, [Management.Automation.Language.VariableExpressionAst]$Variable) {
	$variableName = $Variable.VariablePath.UserPath
	if ($variableName -match '^(global|script|local|private):(.+)$') {
		$variableName = $variableName.Substring($variableName.IndexOf(':') + 1)
	}
	if ($protectedNames -contains $variableName.ToLowerInvariant()) {
		$findings.Add(@{ label = $Label; kind = $Kind; variable = $Variable.VariablePath.UserPath; line = $Variable.Extent.StartLineNumber })
	}
}

foreach ($block in $blocks) {
	$tokens = $null
	$parseErrors = $null
	$syntax = [Management.Automation.Language.Parser]::ParseInput($block.code, [ref]$tokens, [ref]$parseErrors)
	if ($parseErrors.Count) { throw "$($block.label): $($parseErrors | Out-String)" }
	foreach ($statement in $syntax.FindAll({ param($candidate) $candidate -is [Management.Automation.Language.AssignmentStatementAst] }, $true)) {
		foreach ($targetVariable in @(Get-WriteTargets $statement.Left)) {
			Add-Finding $block.label 'assignment' $targetVariable
		}
	}
	foreach ($loopStatement in $syntax.FindAll({ param($candidate) $candidate -is [Management.Automation.Language.ForEachStatementAst] }, $true)) {
		Add-Finding $block.label 'foreach' $loopStatement.Variable
	}
	foreach ($parameterNode in $syntax.FindAll({ param($candidate) $candidate -is [Management.Automation.Language.ParameterAst] }, $true)) {
		Add-Finding $block.label 'parameter' $parameterNode.Name
	}
	foreach ($unaryNode in $syntax.FindAll({ param($candidate) $candidate -is [Management.Automation.Language.UnaryExpressionAst] }, $true)) {
		if ($unaryNode.TokenKind.ToString() -in @('PlusPlus', 'MinusMinus', 'PostfixPlusPlus', 'PostfixMinusMinus')) {
			foreach ($targetVariable in @(Get-WriteTargets $unaryNode.Child)) { Add-Finding $block.label 'increment' $targetVariable }
		}
	}
}

@{ protected_names = $protectedNames; readonly_names = $readOnlyNames; scanned = @($blocks).Count; violations = @($findings.ToArray()) } | ConvertTo-Json -Depth 8 -Compress
exit 0
