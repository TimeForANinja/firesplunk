import logging
import pymongo


def init_db(db: pymongo.database.Database, index_manager=None):
    # Following collections are expected:
    # summaries -> list of all records
    # data_status -> status of each date
    # correlated_rule_ip -> correlated data for each IP/rule pair
    # correlated_rule_ports -> correlated data for each rule/port pair

    # Ensure indexes exist
    db['summaries'].create_index("date")
    db['summaries'].create_index([("src_ip", 1), ("dest_ip", 1), ("rule", 1), ("date", 1)], unique=True)
    db['summaries'].create_index("expires_at", expireAfterSeconds=0)
    db['data_status'].create_index("date", unique=True)
    db['correlated_rule_ip'].create_index([("ip", 1), ("rule_id", 1), ("direction", 1)])
    db['correlated_rule_ip'].create_index("expires_at", expireAfterSeconds=0)
    db['correlated_rule_ports'].create_index([("rule_id", 1), ("port", 1)])
    db['correlated_rule_ports'].create_index("expires_at", expireAfterSeconds=0)
    db['tasks'].create_index("created_at")
    db['tasks'].create_index("last_state_change")

    # Initialize data_status from existing summaries
    logging.info("Initializing data_status from existing summaries...")
    pipeline = [
        {'$group': {
            '_id': '$date',
            'count': {'$sum': 1},
            'uploaded_at': {'$first': '$uploaded_at'}
        }}
    ]
    results = list(db['summaries'].aggregate(pipeline))
    for res in results:
        db['data_status'].update_one(
            {'date': res['_id']},
            {'$set': {
                'status': 'present',
                'count': res['count'],
                'uploaded_at': res['uploaded_at']
            }},
            upsert=True
        )

    # initialize correlated collections if empty
    if db['correlated_rule_ip'].count_documents({}) == 0 or db['correlated_rule_ports'].count_documents({}) == 0:
        if index_manager:
            index_manager.add_task('BUILD_INDEX')
        else:
            build_correlations(db)

    logging.info(f"Database initialization complete.")
    return db


def build_correlations(db: pymongo.database.Database, progress_callback=None):
    logging.info("building correlated data from summaries...")

    if progress_callback:
        progress_callback(0, "Rebuilding correlation indexes")
    # Use $out to write directly on the server side - no client round-trips,
    # no per-doc upserts. We build both directions in a single pass over the
    # collection using $facet-style logic via $project + $unwind.
    pipeline_corr = [
        # Emit two docs per summary (one for src, one for dst) so we only
        # scan `summaries` ONCE instead of twice.
        {'$project': {
            '_id': 0,
            'rule': 1,
            'date': 1,
            'count': 1,
            'expires_at': 1,
            '_dirs': [
                {'ip': '$src_ip', 'direction': 'src'},
                {'ip': '$dest_ip', 'direction': 'dst'},
            ],
        }},
        {'$unwind': '$_dirs'},
        {'$group': {
            '_id': {
                'ip': '$_dirs.ip',
                'rule_id': '$rule',
                'direction': '$_dirs.direction',
                'date': '$date',
            },
            'count': {'$sum': '$count'},
            'expires_at': {'$max': '$expires_at'},
        }},
        {'$group': {
            '_id': {
                'ip': '$_id.ip',
                'rule_id': '$_id.rule_id',
                'direction': '$_id.direction',
            },
            'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
            'expires_at': {'$max': '$expires_at'},
        }},
        {'$project': {
            '_id': 0,
            'ip': '$_id.ip',
            'rule_id': '$_id.rule_id',
            'direction': '$_id.direction',
            'activity': {'$arrayToObject': '$activity'},
            'expires_at': 1,
        }},
        {'$out': 'correlated_rule_ip'},
    ]
    db['summaries'].aggregate(pipeline_corr, allowDiskUse=True)
    if progress_callback:
        progress_callback(60, "IP correlations done")

    # 2. Rule + Port correlation
    pipeline_ports = [
        {'$unwind': '$ports'},
        {'$group': {
            '_id': {'rule': '$rule', 'port': '$ports', 'date': '$date'},
            'count': {'$sum': '$count'},
            'expires_at': {'$max': '$expires_at'}
        }},
        {'$group': {
            '_id': {'rule': '$_id.rule', 'port': '$_id.port'},
            'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
            'expires_at': {'$max': '$expires_at'}
        }},
        {'$project': {
            '_id': 0,
            'rule_id': '$_id.rule_id',
            'port': '$_id.port',
            'activity': {'$arrayToObject': '$activity'},
            'expires_at': 1,
        }},
        {'$out': 'correlated_rule_ports'},
    ]
    db['summaries'].aggregate(pipeline_ports, allowDiskUse=True)

    if progress_callback:
        progress_callback(95, "Port correlations done. Rebuilding indexes...")

    # $out drops the target collection and replaces it, which also drops
    # indexes - recreate them here.
    db['correlated_rule_ip'].create_index(
        [("ip", 1), ("rule_id", 1), ("direction", 1)]
    )
    db['correlated_rule_ip'].create_index("expires_at", expireAfterSeconds=0)
    db['correlated_rule_ports'].create_index([("rule_id", 1), ("port", 1)])
    db['correlated_rule_ports'].create_index("expires_at", expireAfterSeconds=0)

    if progress_callback:
        progress_callback(100, "Done")

    logging.info("Correlated data initialization complete")
