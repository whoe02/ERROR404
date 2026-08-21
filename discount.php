<?php
// discount.php — PHP raw-query test fixture bug for Autocure.
//
// applyDiscount() runs a raw, unparameterized SQL query for a discount
// code and reads the result with no check that a row was actually found.
// An unknown code makes fetch_assoc() return null, and accessing
// $row['rate'] on it raises a real PHP warning — the same "array offset
// on null" class of bug already used elsewhere in this fixture.

function loadEnv(string $path): array {
    $env = [];
    foreach (file($path) as $line) {
        $line = trim($line);
        if ($line === '' || str_starts_with($line, '#') || !str_contains($line, '=')) {
            continue;
        }
        [$key, $value] = explode('=', $line, 2);
        $env[trim($key)] = trim($value);
    }
    return $env;
}

function applyDiscount(mysqli $db, int $subtotalCents, string $discountCode): int {
    // INTENTIONAL BUG: raw, unparameterized query; no check that a row was
    // actually found before reading it.
    $result = $db->query("SELECT rate FROM discount_codes WHERE code = '{$discountCode}'");
    $row = $result->fetch_assoc();
    $rate = $row['rate'];
    return (int) round($subtotalCents * (1 - $rate));
}

function reportToAutocure(array $env, Throwable $e, string $branch, string $commitSha): array {
    $payload = [
        'type' => 'exception',
        'environment' => 'staging',
        'data' => [
            'exception_type' => get_class($e),
            'message' => $e->getMessage(),
            'stack_trace' => $e->getTraceAsString(),
            'request_path' => '/discount.php',
            'branch' => $branch,
            'commit_sha' => $commitSha,
        ],
    ];
    $ch = curl_init(rtrim($env['AUTOCURE_URL'], '/') . '/events');
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_HTTPHEADER => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . $env['AUTOCURE_API_KEY'],
            'Idempotency-Key: php-discount-' . bin2hex(random_bytes(8)),
        ],
        CURLOPT_POSTFIELDS => json_encode($payload),
    ]);
    $response = curl_exec($ch);
    $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    return ['status' => $status, 'body' => $response];
}

$env = loadEnv(__DIR__ . '/.env');

// Convert PHP warnings/notices into catchable exceptions.
set_error_handler(function (int $severity, string $message, string $file, int $line) {
    throw new ErrorException($message, 0, $severity, $file, $line);
});

$db = new mysqli(
    $env['DB_HOST'],
    $env['DB_USER'],
    $env['DB_PASSWORD'],
    $env['DB_NAME'],
    (int) $env['DB_PORT']
);

$branch = trim((string) shell_exec('git -C ' . escapeshellarg(__DIR__) . ' rev-parse --abbrev-ref HEAD'));
$commitSha = trim((string) shell_exec('git -C ' . escapeshellarg(__DIR__) . ' rev-parse HEAD'));
$discountCode = $argv[1] ?? 'BOGUSCODE';

try {
    $total = applyDiscount($db, 1999, $discountCode);
    echo "OK: total = {$total}\n";
} catch (Throwable $e) {
    echo "CRASHED: " . get_class($e) . ": " . $e->getMessage() . "\n";
    $report = reportToAutocure($env, $e, $branch, $commitSha);
    echo "Reported to Autocure: HTTP {$report['status']}\n";
    echo $report['body'] . "\n";
}
